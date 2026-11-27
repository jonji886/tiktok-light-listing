// macOS Vision OCR：输出文字 + 归一化包围盒（原点左下），供后续擦除/回贴用
// 用法: swift ocr_vision.swift <图片路径> [lang...]
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
    FileHandle.standardError.write("usage: ocr_vision <img> [langs]\n".data(using: .utf8)!)
    exit(2)
}
let path = args[1]
let langs = args.count > 2 ? Array(args[2...]) : ["zh-Hans", "en-US"]

guard let cg = cgImage(path) else {
    print("{\"error\":\"cannot load image\"}")
    exit(1)
}
let W = Double(cg.width), H = Double(cg.height)

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = langs
req.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([req]) } catch {
    print("{\"error\":\"perform failed\"}")
    exit(1)
}

var items: [[String: Any]] = []
for obs in (req.results ?? []) {
    guard let cand = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox            // normalized, origin bottom-left
    // 转成像素坐标，原点左上
    let x = b.minX * W
    let y = (1 - b.maxY) * H
    let w = b.width * W
    let h = b.height * H
    items.append([
        "text": cand.string,
        "conf": Double(cand.confidence),
        "x": (x).rounded(),
        "y": (y).rounded(),
        "w": (w).rounded(),
        "h": (h).rounded()
    ])
}
let out: [String: Any] = [
    "file": path,
    "width": Int(W),
    "height": Int(H),
    "count": items.count,
    "items": items
]
let data = try! JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
