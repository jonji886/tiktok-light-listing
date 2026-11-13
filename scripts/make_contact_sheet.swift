import AppKit
import Foundation

// 用法: swift make_contact_sheet.swift <manifest.json>
// manifest: { "输出路径": [ {"label":"main_01","path":"/abs/x.jpg"}, ... ], ... }
// 把每个品的两通道图带编号拼成一张总览，供多模态整图分类使用。

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write("usage: swift make_contact_sheet.swift <manifest.json>\n".data(using: .utf8)!)
    exit(1)
}
let manifestPath = CommandLine.arguments[1]
let data = try Data(contentsOf: URL(fileURLWithPath: manifestPath))
guard let root = try JSONSerialization.jsonObject(with: data) as? [String: [[String: String]]] else {
    FileHandle.standardError.write("bad manifest\n".data(using: .utf8)!)
    exit(1)
}

let cell: CGFloat = 280
let cols = 4
let labelH: CGFloat = 24

for (outPath, items) in root {
    let rows = max(1, (items.count + cols - 1) / cols)
    let W = cell * CGFloat(cols)
    let H = cell * CGFloat(rows)
    let canvas = NSImage(size: NSSize(width: W, height: H))
    canvas.lockFocus()
    NSColor(calibratedWhite: 0.94, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: W, height: H).fill()

    for (i, item) in items.enumerated() {
        let c = i % cols
        let r = i / cols
        let x = cell * CGFloat(c)
        let y = H - cell * CGFloat(r + 1)

        NSColor.white.setFill()
        NSRect(x: x + 2, y: y + 2, width: cell - 4, height: cell - 4).fill()

        let availH = cell - labelH - 10
        if let p = item["path"], let src = NSImage(contentsOfFile: p) {
            let sz = src.size
            if sz.width > 0 && sz.height > 0 {
                let scale = min((cell - 12) / sz.width, availH / sz.height)
                let dw = sz.width * scale
                let dh = sz.height * scale
                let dr = NSRect(x: x + (cell - dw) / 2,
                                y: y + 5 + (availH - dh) / 2,
                                width: dw, height: dh)
                src.draw(in: dr, from: .zero, operation: .sourceOver, fraction: 1.0)
            }
        }

        NSColor(calibratedWhite: 0.12, alpha: 0.88).setFill()
        NSRect(x: x + 2, y: y + cell - labelH - 2, width: cell - 4, height: labelH).fill()
        let label = (item["label"] ?? "?") as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: NSColor.white,
            .font: NSFont.boldSystemFont(ofSize: 15)
        ]
        label.draw(at: NSPoint(x: x + 8, y: y + cell - labelH + 2), withAttributes: attrs)
    }
    canvas.unlockFocus()

    guard let tiff = canvas.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let jpg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.82]) else {
        print("FAIL \(outPath)")
        continue
    }
    try jpg.write(to: URL(fileURLWithPath: outPath))
    print("OK \(items.count) cells -> \(outPath)")
}
