import SwiftUI

/// Buddy's face (orange disc, cream face, two eyes) drawn with shapes so it stays sharp in the tiny
/// compact island and needs no image assets in the widget extension.
struct BuddyFace: View {
    var size: CGFloat
    var phase: BuddyPhase = .ready

    static let orange = Color(red: 1.0, green: 132 / 255, blue: 36 / 255)
    static let cream = Color(red: 1.0, green: 246 / 255, blue: 236 / 255)
    static let ink = Color(red: 43 / 255, green: 33 / 255, blue: 52 / 255)

    var body: some View {
        ZStack {
            Circle().fill(Self.orange)
            Circle().fill(Self.cream).padding(size * 0.14)
            HStack(spacing: size * 0.16) {
                eye
                eye
            }
            .offset(y: phase == .looking ? -size * 0.02 : -size * 0.04)
            if phase == .answer || phase == .ready {
                SmileShape()
                    .stroke(Self.ink, style: StrokeStyle(lineWidth: max(1, size * 0.06), lineCap: .round))
                    .frame(width: size * 0.26, height: size * 0.1)
                    .offset(y: size * 0.16)
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel("Buddy")
    }

    private var eye: some View {
        Capsule()
            .fill(Self.ink)
            .frame(width: size * 0.1, height: phase == .thinking ? size * 0.06 : size * 0.15)
    }
}

private struct SmileShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY), control: CGPoint(x: rect.midX, y: rect.maxY * 1.6))
        return path
    }
}
