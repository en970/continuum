// Generates the Continuum app icon: 1024x1024 PNG.
// Usage: swift makeicon.swift <out.png>
// Design: squircle plate on the macOS icon grid (indigo -> purple),
// with an open circular arrow standing for uninterrupted continuity.

import AppKit
import CoreGraphics

let canvas = 1024.0
let inset = 100.0                     // margin from the Apple icon grid
let plate = canvas - inset * 2        // 824 — the plate
let radius = plate * 0.2237           // macOS squircle corner ratio

guard let ctx = CGContext(data: nil,
                          width: Int(canvas), height: Int(canvas),
                          bitsPerComponent: 8, bytesPerRow: 0,
                          space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
else { fatalError("could not create bitmap") }

let plateRect = CGRect(x: inset, y: inset, width: plate, height: plate)
let platePath = CGPath(roundedRect: plateRect,
                       cornerWidth: radius, cornerHeight: radius,
                       transform: nil)

// --- Plate: vertical gradient ---
ctx.saveGState()
ctx.addPath(platePath)
ctx.clip()

let cs = CGColorSpaceCreateDeviceRGB()
let top = CGColor(colorSpace: cs, components: [0.42, 0.36, 0.90, 1.0])!   // indigo
let bottom = CGColor(colorSpace: cs, components: [0.30, 0.22, 0.62, 1.0])! // deep purple
if let g = CGGradient(colorsSpace: cs, colors: [top, bottom] as CFArray, locations: [0, 1]) {
    ctx.drawLinearGradient(g,
                           start: CGPoint(x: 0, y: canvas - inset),
                           end: CGPoint(x: 0, y: inset),
                           options: [])
}

// Thin gloss along the top edge — the volume cue macOS icons have
if let gloss = CGGradient(colorsSpace: cs,
                          colors: [CGColor(colorSpace: cs, components: [1, 1, 1, 0.16])!,
                                   CGColor(colorSpace: cs, components: [1, 1, 1, 0.0])!] as CFArray,
                          locations: [0, 1]) {
    ctx.drawLinearGradient(gloss,
                           start: CGPoint(x: 0, y: canvas - inset),
                           end: CGPoint(x: 0, y: canvas - inset - plate * 0.45),
                           options: [])
}
ctx.restoreGState()

// --- Symbol: open circular arrow ---
let center = CGPoint(x: canvas / 2, y: canvas / 2)
let ringR = plate * 0.27
let lineW = plate * 0.105

ctx.saveGState()
ctx.setStrokeColor(CGColor(colorSpace: cs, components: [1, 1, 1, 1])!)
ctx.setLineWidth(lineW)
ctx.setLineCap(.round)

// Yay: sag alttan baslar, saat yonunun tersine donup sag ustte biter.
// Ok ucu bitis noktasina oturur, bu yuzden yay ok ucunun tabani kadar kisaltilir.
let gapStart = -0.30 * Double.pi     // ~ -54°  (sag alt uc)
let gapEnd = 0.42 * Double.pi        // ~ +76°  (sag ust uc — ok burada)

let headH = lineW * 1.30             // arrowhead height
let headW = lineW * 0.95             // half the arrowhead base
let headSpan = headH / ringR         // angle the head occupies on the arc

// Motion starts bottom right, sweeps left, ends top right (decreasing angle).
ctx.addArc(center: center, radius: ringR,
           startAngle: gapStart + 2 * Double.pi, endAngle: gapEnd + headSpan,
           clockwise: true)
ctx.strokePath()

// Arrowhead: base where the arc ends, tip pointing along the motion
let baseAngle = gapEnd + headSpan
let base = CGPoint(x: center.x + cos(baseAngle) * ringR,
                   y: center.y + sin(baseAngle) * ringR)
let tip = CGPoint(x: center.x + cos(gapEnd) * ringR,
                  y: center.y + sin(gapEnd) * ringR)
// Outward normal at the base
let nx = cos(baseAngle), ny = sin(baseAngle)

ctx.setFillColor(CGColor(colorSpace: cs, components: [1, 1, 1, 1])!)
ctx.move(to: tip)
ctx.addLine(to: CGPoint(x: base.x + nx * headW, y: base.y + ny * headW))
ctx.addLine(to: CGPoint(x: base.x - nx * headW, y: base.y - ny * headW))
ctx.closePath()
ctx.fillPath()

// Centre dot: where the work stops, and picks up again
ctx.setFillColor(CGColor(colorSpace: cs, components: [1, 1, 1, 0.92])!)
ctx.fillEllipse(in: CGRect(x: center.x - lineW * 0.55, y: center.y - lineW * 0.55,
                           width: lineW * 1.1, height: lineW * 1.1))
ctx.restoreGState()

// --- Save ---
guard let image = ctx.makeImage() else { fatalError("could not render image") }
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let rep = NSBitmapImageRep(cgImage: image)
guard let data = rep.representation(using: .png, properties: [:]) else {
    fatalError("could not encode png")
}
try! data.write(to: URL(fileURLWithPath: out))
print("wrote: \(out)")
