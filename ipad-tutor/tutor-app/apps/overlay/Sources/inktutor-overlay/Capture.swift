import AppKit
import ImageIO
import OverlayCore
import ScreenCaptureKit

/// Single frames of one display with ScreenCaptureKit (`SCScreenshotManager`, macOS 14): the helper's own windows
/// (overlay, phantom icon, bubble, transcript) and every blocklisted app are excluded by the content filter, so
/// neither our highlights nor a password manager ever reach a frame. Downscaled to ≤ 1280 px wide, JPEG q 0.6.
final class ScreenCapturer {
    private var content: SCShareableContent?
    private var contentAt = 0.0
    private(set) var busy = false
    var maxWidth = 1280
    var quality = 0.6

    struct Shot {
        var jpeg: Data
        var width: Int
        var height: Int
        var scale: Double
        var ms: Double
    }

    /// Forget the cached window list (a new app came to the front: its windows must be known to exclude them).
    func invalidate() { content = nil }

    func capture(display: Display, globalFrame: Rect, blocklist: [String], done: @escaping (Result<Shot, Error>) -> Void) {
        guard !busy else { return }
        busy = true
        let t0 = ProcessInfo.processInfo.systemUptime
        withContent { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let e):
                busy = false
                done(.failure(e))
            case .success(let content):
                guard let scd = content.displays.first(where: { Int($0.displayID) == display.id }) else {
                    busy = false
                    self.content = nil
                    done(.failure(CaptureError("display \(display.id) not in ScreenCaptureKit's list")))
                    return
                }
                let me = getpid()
                let excluded = content.applications.filter {
                    $0.processID == me || isBlocked(app: $0.applicationName, bundleId: $0.bundleIdentifier, blocklist: blocklist)
                }
                let filter = SCContentFilter(display: scd, excludingApplications: excluded, exceptingWindows: [])
                let size = FrameMath.size(points: globalFrame.w, globalFrame.h, backingScale: display.scale, maxWidth: maxWidth)
                let cfg = SCStreamConfiguration()
                cfg.width = size.width
                cfg.height = size.height
                cfg.showsCursor = false
                cfg.scalesToFit = true
                let q = quality
                SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { image, error in
                    let jpeg = image.flatMap { Self.jpeg($0, quality: q) }
                    DispatchQueue.main.async {
                        self.busy = false
                        if let jpeg, let image {
                            let ms = (ProcessInfo.processInfo.systemUptime - t0) * 1000
                            done(.success(Shot(jpeg: jpeg, width: image.width, height: image.height, scale: Double(image.width) / globalFrame.w, ms: ms)))
                        } else {
                            done(.failure(error ?? CaptureError("no image")))
                        }
                    }
                }
            }
        }
    }

    private func withContent(_ k: @escaping (Result<SCShareableContent, Error>) -> Void) {
        let now = ProcessInfo.processInfo.systemUptime
        if let c = content, now - contentAt < 3 { k(.success(c)); return }
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { c, err in
            DispatchQueue.main.async {
                if let c {
                    self.content = c
                    self.contentAt = ProcessInfo.processInfo.systemUptime
                    k(.success(c))
                } else {
                    k(.failure(err ?? CaptureError("no shareable content")))
                }
            }
        }
    }

    static func jpeg(_ img: CGImage, quality: Double) -> Data? {
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out as CFMutableData, "public.jpeg" as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, img, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        return CGImageDestinationFinalize(dest) ? out as Data : nil
    }
}

struct CaptureError: Error, CustomStringConvertible {
    var description: String
    init(_ d: String) { description = d }
}

/// The frontmost app's frontmost normal window, in global points (CGWindowList bounds need no permission).
func frontWindowFrame(pid: pid_t) -> Rect? {
    guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    for w in list {
        guard (w[kCGWindowOwnerPID as String] as? Int).map({ pid_t($0) }) == pid,
            (w[kCGWindowLayer as String] as? Int) == 0,
            let b = w[kCGWindowBounds as String] as? NSDictionary,
            let r = CGRect(dictionaryRepresentation: b), r.width > 40, r.height > 40
        else { continue }
        return Rect(x: Double(r.minX), y: Double(r.minY), w: Double(r.width), h: Double(r.height))
    }
    return nil
}
