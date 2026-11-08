import SwiftUI
import WidgetKit

@main
struct BuddyWidgetsBundle: WidgetBundle {
    var body: some Widget {
        BuddyLiveActivity()
        AskBuddyWidget()
    }
}
