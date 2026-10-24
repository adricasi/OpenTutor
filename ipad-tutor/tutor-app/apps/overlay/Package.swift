// swift-tools-version:5.9
// Ink Tutor screen-tutor helper (docs/SCREEN-TUTOR.md). Apple frameworks only: no package dependencies.
//   swift build -c release --product inktutor-overlay   (make overlay-build wraps it in a .app and signs it ad hoc)
//   swift run overlay-selftest                          (pure-logic checks; the Command Line Tools have no XCTest)
import PackageDescription

let package = Package(
    name: "InkTutorOverlay",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "inktutor-overlay", targets: ["inktutor-overlay"]),
        .executable(name: "overlay-selftest", targets: ["overlay-selftest"]),
    ],
    targets: [
        // No UI: protocol Codable structs, coordinates, layout, overlay state, options, the status model, the AX
        // walker over any tree, frame maths, the recorder, and audio DSP (AVFAudio: resampler, PCM16, barge-in).
        .target(name: "OverlayCore", path: "Sources/OverlayCore"),
        // AppKit: overlay windows, phantom icon + bubble + menu, WebSocket client, voice I/O (AVAudioEngine),
        // perception (ScreenCaptureKit frames, AX reader, input monitors).
        .executableTarget(name: "inktutor-overlay", dependencies: ["OverlayCore"], path: "Sources/inktutor-overlay"),
        .executableTarget(name: "overlay-selftest", dependencies: ["OverlayCore"], path: "Sources/overlay-selftest"),
    ]
)
