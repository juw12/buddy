import SwiftUI
import WidgetKit

/// Home Screen / Lock Screen shortcut: one tap opens Buddy's ask box (and puts Buddy in the island).
struct AskBuddyWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "AskBuddy", provider: AskBuddyProvider()) { _ in
            AskBuddyWidgetView()
                .widgetURL(URL(string: "buddy://ask"))
        }
        .configurationDisplayName("Ask Buddy")
        .description("One tap to ask Buddy, or to ask about your last screenshot.")
        .supportedFamilies([.systemSmall, .accessoryCircular, .accessoryRectangular])
    }
}

struct AskBuddyEntry: TimelineEntry {
    let date: Date
}

struct AskBuddyProvider: TimelineProvider {
    func placeholder(in context: Context) -> AskBuddyEntry {
        AskBuddyEntry(date: Date())
    }

    func getSnapshot(in context: Context, completion: @escaping (AskBuddyEntry) -> Void) {
        completion(AskBuddyEntry(date: Date()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<AskBuddyEntry>) -> Void) {
        completion(Timeline(entries: [AskBuddyEntry(date: Date())], policy: .never))
    }
}

struct AskBuddyWidgetView: View {
    @Environment(\.widgetFamily) private var family

    var body: some View {
        Group {
            switch family {
            case .accessoryCircular:
                ZStack {
                    AccessoryWidgetBackground()
                    BuddyFace(size: 34)
                }
            case .accessoryRectangular:
                HStack(spacing: 8) {
                    BuddyFace(size: 30)
                    VStack(alignment: .leading) {
                        Text("Ask Buddy").font(.headline)
                        Text("Tap to ask").font(.caption)
                    }
                }
            default:
                VStack(alignment: .leading, spacing: 10) {
                    BuddyFace(size: 46)
                    Spacer(minLength: 0)
                    Text("Ask Buddy")
                        .font(.headline)
                        .foregroundStyle(.white)
                    Text("Anything, or your last screenshot")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.75))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .containerBackground(for: .widget) {
            Color.black
        }
    }
}
