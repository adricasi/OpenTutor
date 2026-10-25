import Foundation

/// `--record DIR`: every message the helper sends, for test fixtures (`ink-tutor/fixtures/screens/` is git-ignored).
/// `DIR/messages.jsonl` gets one wire message per line, exactly as sent, except that a `screen.frame`'s `jpeg_b64` is
/// written to `DIR/<frame id>.jpg` and replaced by `"@<frame id>.jpg"` (still a valid ScreenFrame; a replay puts the
/// base64 back). Writes happen on a background queue, in order.
public final class Recorder {
    public let dir: URL
    private let queue = DispatchQueue(label: "inktutor.recorder")
    private let handle: FileHandle
    public private(set) var count = 0

    public init(dir: String) throws {
        let url = URL(fileURLWithPath: (dir as NSString).expandingTildeInPath, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        let file = url.appendingPathComponent("messages.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) { FileManager.default.createFile(atPath: file.path, contents: nil) }
        handle = try FileHandle(forWritingTo: file)
        handle.seekToEndOfFile()
        self.dir = url
    }

    deinit { try? handle.close() }

    /// One encoded wire message.
    public func record(_ json: String) {
        count += 1
        queue.async { [handle, dir] in
            let line = Recorder.line(for: json) { name, jpeg in try? jpeg.write(to: dir.appendingPathComponent(name)) }
            handle.write(Data((line + "\n").utf8))
        }
    }

    /// The JSONL line for `json`; a frame's JPEG goes to `save(fileName, bytes)`. Pure apart from `save`.
    public static func line(for json: String, save: (String, Data) -> Void) -> String {
        guard json.contains("\"screen.frame\""),
            var obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any],
            obj["type"] as? String == "screen.frame", let b64 = obj["jpeg_b64"] as? String,
            let id = obj["id"] as? String, let jpeg = Data(base64Encoded: b64)
        else { return json }
        let safe = String(id.map { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" ? $0 : "_" })
        let name = "\(safe).jpg"
        save(name, jpeg)
        obj["jpeg_b64"] = "@\(name)"
        guard let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes]) else { return json }
        return String(decoding: d, as: UTF8.self)
    }

    public func flush() { queue.sync { try? handle.synchronize() } }
}
