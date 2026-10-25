import AVFoundation
import AppKit
import ApplicationServices
import CoreGraphics

/// The three macOS permissions the helper uses. Everything degrades: without Screen Recording there are no frames,
/// without Accessibility no AX snapshots and no key events, without the microphone no Listen. Highlights and the
/// spoken replies need none of them.
enum Permission: String, CaseIterable {
    case microphone, screen, accessibility

    var granted: Bool {
        switch self {
        case .microphone: return AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        case .screen: return CGPreflightScreenCaptureAccess()
        case .accessibility: return AXIsProcessTrusted()
        }
    }

    var menuTitle: String {
        switch self {
        case .microphone: return "Allow microphone…"
        case .screen: return "Allow screen recording…"
        case .accessibility: return "Allow accessibility…"
        }
    }

    /// What the user loses without it (the status bubble).
    var missingText: String {
        switch self {
        case .microphone: return "No microphone access: Listen can't hear you"
        case .screen: return "No Screen Recording access: the tutor can't see the screen"
        case .accessibility: return "No Accessibility access: the tutor can't find buttons by name"
        }
    }

    private var settingsPane: String {
        switch self {
        case .microphone: return "Privacy_Microphone"
        case .screen: return "Privacy_ScreenCapture"
        case .accessibility: return "Privacy_Accessibility"
        }
    }

    func openSettings() {
        if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(settingsPane)") { NSWorkspace.shared.open(u) }
    }

    /// Ask macOS (its own prompt the first time), else open the right System Settings pane. `done` gets a note.
    func request(_ done: @escaping (String) -> Void) {
        switch self {
        case .microphone:
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .audio) { ok in
                    DispatchQueue.main.async { done(ok ? "Microphone allowed" : "Microphone denied — turn it on in System Settings → Privacy & Security → Microphone") }
                }
            case .authorized: done("Microphone already allowed")
            default:
                openSettings()
                done("Turn on the helper (or your terminal) under Microphone, then try Listen again")
            }
        case .screen:
            if !CGRequestScreenCaptureAccess() {  // prompts only the first time; after that it just says no
                openSettings()
                done("Turn on the helper (or your terminal) under Screen Recording, then quit and restart the helper")
            } else {
                done("Screen recording allowed")
            }
        case .accessibility:
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            if !AXIsProcessTrustedWithOptions(opts) {
                openSettings()
                done("Turn on the helper (or your terminal) under Accessibility; it takes effect within a few seconds")
            } else {
                done("Accessibility allowed")
            }
        }
    }

    static var missing: [Permission] { allCases.filter { !$0.granted } }
}
