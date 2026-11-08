import ActivityKit
import SwiftUI
import WidgetKit

/// Buddy living in the Dynamic Island (and on the Lock Screen of iPhones without one).
///  - compact: Buddy's face left of the camera, what it is doing on the right
///  - minimal: just the face (when another app shares the island)
///  - expanded (long-press): the question, the answer, and buttons to ask again
struct BuddyLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: BuddyAttributes.self) { context in
            LockScreenBuddy(state: context.state)
                .activityBackgroundTint(Color.black.opacity(0.88))
                .activitySystemActionForegroundColor(.white)
                .widgetURL(URL(string: "buddy://ask"))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    BuddyFace(size: 40, phase: context.state.phase)
                        .padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Label(context.state.phase.status, systemImage: context.state.phase.symbol)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(statusColor(context.state.phase))
                        .labelStyle(.titleAndIcon)
                        .lineLimit(1)
                        .padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(ReplyText.short(context.state.body, limit: 220))
                            .font(.callout)
                            .foregroundStyle(.white.opacity(0.92))
                            .lineLimit(4)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        HStack(spacing: 8) {
                            Link(destination: URL(string: "buddy://ask")!) {
                                Label("Ask", systemImage: "keyboard")
                                    .islandButton()
                            }
                            Link(destination: URL(string: "buddy://ask?screenshot=1")!) {
                                Label("My last screenshot", systemImage: "camera.viewfinder")
                                    .islandButton()
                            }
                        }
                    }
                    .padding(.horizontal, 4)
                }
            } compactLeading: {
                BuddyFace(size: 22, phase: context.state.phase)
            } compactTrailing: {
                Image(systemName: context.state.phase.symbol)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(statusColor(context.state.phase))
            } minimal: {
                BuddyFace(size: 20, phase: context.state.phase)
            }
            .widgetURL(URL(string: "buddy://ask"))
            .keylineTint(BuddyFace.orange)
        }
    }
}

func statusColor(_ phase: BuddyPhase) -> Color {
    switch phase {
    case .trouble: return .red
    case .answer: return .green
    case .ready: return BuddyFace.orange
    default: return .white
    }
}

private struct LockScreenBuddy: View {
    let state: BuddyAttributes.ContentState

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            BuddyFace(size: 44, phase: state.phase)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(state.title)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Spacer(minLength: 6)
                    Label(state.phase.status, systemImage: state.phase.symbol)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(statusColor(state.phase))
                }
                Text(ReplyText.short(state.body, limit: 200))
                    .font(.callout)
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(3)
            }
        }
        .foregroundStyle(.white)
        .padding(14)
    }
}

private extension View {
    func islandButton() -> some View {
        self.font(.caption.weight(.semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Capsule().fill(BuddyFace.orange))
    }
}
