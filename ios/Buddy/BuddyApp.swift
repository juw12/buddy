import SwiftUI

@main
struct BuddyApp: App {
    @StateObject private var model = BuddyModel.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var startedCIHelpers = false

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .onOpenURL { url in
                    model.handle(url)
                }
                .task {
                    await runLaunchArguments()
                    await model.checkForUpdate()
                }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.appearedInForeground()
            }
        }
    }

    /// CI: -demo answer|thinking|ready, -ask "question", -selftest
    private func runLaunchArguments() async {
        guard !startedCIHelpers else { return }
        startedCIHelpers = true
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "-demo"), index + 1 < arguments.count {
            model.runDemo(arguments[index + 1])
        } else if let index = arguments.firstIndex(of: "-ask"), index + 1 < arguments.count {
            model.ask(arguments[index + 1], jpeg: nil)
        } else if arguments.contains("-selftest") {
            await model.runSelfTest()
        }
    }
}
