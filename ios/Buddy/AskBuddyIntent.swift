import AppIntents
import UIKit
import UniformTypeIdentifiers

/// "Ask Buddy" for Shortcuts, Siri, Back Tap and the Action button.
/// With a picture (e.g. Take Screenshot -> Ask Buddy) it answers in the Dynamic Island without opening the app.
struct AskBuddyIntent: AppIntent, LiveActivityIntent {
    static var title: LocalizedStringResource = "Ask Buddy"
    static var description = IntentDescription("Ask Buddy a question, optionally about a screenshot. The answer appears in the Dynamic Island.")

    @Parameter(title: "Question", default: "")
    var question: String

    @Parameter(title: "Picture", supportedContentTypes: [.image])
    var picture: IntentFile?

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<String> {
        var jpeg: Data?
        if let picture, let image = UIImage(data: picture.data) {
            jpeg = BrainClient.jpeg(from: image)
        }
        var text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            text = jpeg == nil ? "Say hi and tell me in one sentence what you can help with." :
                "What is on this screen, and what should I do next?"
        }
        let model = BuddyModel.shared
        model.ask(text, jpeg: jpeg)
        // Wait for the answer (it streams into the island meanwhile).
        for _ in 0..<120 {
            try await Task.sleep(nanoseconds: 500_000_000)
            if !model.phase.isBusy { break }
        }
        let answer = model.answer.isEmpty ? "Buddy couldn't answer just now." : model.answer
        return .result(value: answer, dialog: IntentDialog(stringLiteral: answer))
    }
}

/// Opens Buddy ready to type (no picture, no question).
struct OpenBuddyIntent: AppIntent {
    static var title: LocalizedStringResource = "Open Buddy"
    static var description = IntentDescription("Open Buddy's ask box.")
    static var openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        BuddyModel.shared.focusAsk = true
        return .result()
    }
}

struct BuddyShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskBuddyIntent(),
            phrases: ["Ask \(.applicationName)", "Ask \(.applicationName) about my screen"],
            shortTitle: "Ask Buddy",
            systemImageName: "sparkle"
        )
        AppShortcut(
            intent: OpenBuddyIntent(),
            phrases: ["Open \(.applicationName)"],
            shortTitle: "Open Buddy",
            systemImageName: "keyboard"
        )
    }
}
