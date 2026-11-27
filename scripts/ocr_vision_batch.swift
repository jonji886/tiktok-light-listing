// 批量 macOS Vision OCR：一次进程处理多张图，省掉 N-1 次 swift 冷启动。
// 用法: swift ocr_vision_batch.swift <img1> <img2> ... [--fast] [--lang zh-Hans,en-US]
// 输出: JSON 数组，每项与单图版 ocr_vision.swift 的 JSON 同构（多一项 ok 标记）。
import Foundation
import Vision
import AppKit

func cgImage(_ path: String) -> CGImage? {
    guard let img = NSImage(contentsOfFile: path) else { return nil }
    var rect = NSRect(origin: .zero, size: img.size)
    return img.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: ocr_vision_batch <img...> [--fast] [--lang a,b]\n".data(using: .utf8)!)
    exit(2)
}
var fast = false
var langs = ["zh-Hans", "en-US"]
var paths: [String] = []
var i = 1
while i < args.count {
    if args[i] == "--fast" { fast = true; i += 1 }
    else if args[i] == "--lang" && i + 1 < args.count {
        langs = args[i+1].split(separator: ",").map(String.init); i += 2
    } else { paths.append(args[i]); i += 1 }
}

let req = VNRecognizeTextRequest()
req.recognitionLevel = fast ? .fast : .accurate
req.recognitionLanguages = langs
req.usesLanguageCorrection = false

var results: [[String: Any]] = []
for path in paths {
    guard let cg = cgImage(path) else {
        results.append(["file": path, "ok": false, "error": "cannot load image", "width": 0, "height": 0, "items": []])
        continue
    }
    let W = Double(cg.width), H = Double(cg.height)
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([req]) } catch {
        results.append(["file": path, "ok": false, "error": "perform failed", "width": Int(W), "height": Int(H), "items": []])
        continue
    }
    var items: [[String: Any]] = []
    for obs in (req.results ?? []) {
        guard let cand = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        let x = b.minX * W
        let y = (1 - b.maxY) * H
        let w = b.width * W
        let h = b.height * H
        items.append([
            "text": cand.string, "conf": Double(cand.confidence),
            "x": x.rounded(), "y": y.rounded(), "w": w.rounded(), "h": h.rounded()
        ])
    }
    results.append([
        "file": path, "ok": true, "width": Int(W), "height": Int(H),
        "count": items.count, "items": items
    ])
}
let data = try! JSONSerialization.data(withJSONObject: results, options: [])
print(String(data: data, encoding: .utf8)!)
