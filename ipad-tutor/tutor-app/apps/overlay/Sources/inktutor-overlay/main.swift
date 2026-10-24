// Ink Tutor screen-tutor helper (docs/SCREEN-TUTOR.md, Phase 11): a click-through overlay on every display that
// draws the server's OverlayEvents, and a draggable phantom icon with a small menu. It points; it never clicks,
// types or scrolls for the user (non-negotiable 10): there is no code here that posts input events.
import AppKit
import OverlayCore

setvbuf(stdout, nil, _IOLBF, 0)  // line-buffered, so `make overlay` (or a log file) shows lines as they happen

let options: Options
do {
    options = try Options.parse(Array(CommandLine.arguments.dropFirst()))
} catch {
    FileHandle.standardError.write(Data("\(error)\n\n\(Options.usage)\n".utf8))
    exit(2)
}
if options.help {
    print(Options.usage)
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate(options: options)
app.delegate = delegate
app.setActivationPolicy(.accessory)  // no Dock icon, no menu bar (LSUIElement in the .app does the same)
app.run()
