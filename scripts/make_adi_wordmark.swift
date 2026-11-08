// ADI wordmark: deep navy rounded square + hairline border + monospace "ADI"
// 用法：xcrun swift scripts/make_adi_wordmark.swift out.png [size]
import Foundation
import CoreGraphics
import CoreText
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
let outPath = args.count > 1 ? args[1] : "adi-wordmark.png"
let size = args.count > 2 ? (Int(args[2]) ?? 1024) : 1024
let S = CGFloat(size)

let cs = CGColorSpaceCreateDeviceRGB()
guard let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8,
                          bytesPerRow: 0, space: cs,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    exit(1)
}

// 底：深海军蓝，径向提亮到中心偏上
let bg = CGGradient(colorsSpace: cs, colors: [
    CGColor(red: 0.055, green: 0.086, blue: 0.133, alpha: 1),
    CGColor(red: 0.024, green: 0.037, blue: 0.059, alpha: 1),
] as CFArray, locations: [0, 1])!
ctx.drawRadialGradient(bg,
                       startCenter: CGPoint(x: S * 0.5, y: S * 0.56), startRadius: 0,
                       endCenter: CGPoint(x: S * 0.5, y: S * 0.35), endRadius: S * 0.62,
                       options: [])

// 内描边圆角框（两圈：外亮内暗，做出玻璃边缘）
let inset = S * 0.075
let rect = CGRect(x: inset, y: inset, width: S - inset * 2, height: S - inset * 2)
let radius = S * 0.185
let stroke = CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
ctx.addPath(stroke)
ctx.setStrokeColor(CGColor(red: 0.258, green: 0.545, blue: 0.780, alpha: 0.55))
ctx.setLineWidth(S * 0.006)
ctx.strokePath()

let inner = rect.insetBy(dx: S * 0.012, dy: S * 0.012)
let innerPath = CGPath(roundedRect: inner, cornerWidth: radius * 0.93, cornerHeight: radius * 0.93, transform: nil)
ctx.addPath(innerPath)
ctx.setStrokeColor(CGColor(red: 0.05, green: 0.14, blue: 0.22, alpha: 0.9))
ctx.setLineWidth(S * 0.003)
ctx.strokePath()

// 中心一层极淡的青蓝光晕，让字浮起来
let glow = CGGradient(colorsSpace: cs, colors: [
    CGColor(red: 0.18, green: 0.45, blue: 0.72, alpha: 0.30),
    CGColor(red: 0.18, green: 0.45, blue: 0.72, alpha: 0.0),
] as CFArray, locations: [0, 1])!
ctx.drawRadialGradient(glow,
                       startCenter: CGPoint(x: S * 0.5, y: S * 0.48), startRadius: 0,
                       endCenter: CGPoint(x: S * 0.5, y: S * 0.48), endRadius: S * 0.42,
                       options: [])

// 字：等宽，字距明显拉开（对齐参考图标）
let fontSize = S * 0.225
let font = CTFontCreateWithName("Menlo-Regular" as CFString, fontSize, nil)
let chars = ["A", "D", "I"]
var advs: [CGFloat] = []
for ch in chars {
    var u = Array(ch.utf16)[0]
    var g = CGGlyph()
    CTFontGetGlyphsForCharacters(font, &u, &g, 1)
    var a = CGSize.zero
    CTFontGetAdvancesForGlyphs(font, .horizontal, &g, &a, 1)
    advs.append(CGFloat(a.width))
}
let gap = fontSize * 0.30
let total = advs.reduce(0, +) + gap * CGFloat(chars.count - 1)
var x = S * 0.5 - total * 0.5
let baselineY = S * 0.5 - fontSize * 0.355
ctx.setFillColor(CGColor(red: 0.94, green: 0.97, blue: 1.0, alpha: 1))
for (i, ch) in chars.enumerated() {
    var u = Array(ch.utf16)[0]
    var g = CGGlyph()
    CTFontGetGlyphsForCharacters(font, &u, &g, 1)
    var pos = CGPoint(x: x, y: baselineY)
    CTFontDrawGlyphs(font, &g, &pos, 1, ctx)
    x += advs[i] + gap
}

guard let image = ctx.makeImage() else { exit(1) }
let url = URL(fileURLWithPath: outPath)
guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    exit(1)
}
CGImageDestinationAddImage(dest, image, nil)
CGImageDestinationFinalize(dest)
print("wrote \(outPath) \(size)x\(size)")
