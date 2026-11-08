import AVFoundation
import SwiftUI
import UIKit

/// The app's state: the question, the streaming answer, the attached picture, settings and update info.
/// Every answer is mirrored into the Dynamic Island through IslandController.
@MainActor
final class BuddyModel: ObservableObject {
    static let shared = BuddyModel()

    @Published var question = ""
    @Published var askedQuestion = ""
    @Published var answer = ""
    @Published var phase: BuddyPhase = .ready
    @Published var picture: UIImage?
    @Published var notice = ""
    @Published var focusAsk = false
    @Published var updateVersion: String?

    @Published var keepInIsland: Bool {
        didSet {
            IslandController.shared.keepInIsland = keepInIsland
            if keepInIsland {
                IslandController.shared.rest()
            } else if !phase.isBusy {
                IslandController.shared.endAll()
            }
        }
    }

    @Published var readAloud: Bool {
        didSet { UserDefaults.standard.set(readAloud, forKey: "readAloud") }
    }

    private let speaker = AVSpeechSynthesizer()
    private var askTask: Task<Void, Never>?
    private var backgroundTask = UIBackgroundTaskIdentifier.invalid

    init() {
        keepInIsland = IslandController.shared.keepInIsland
        readAloud = UserDefaults.standard.bool(forKey: "readAloud")
    }

    var islandAvailable: Bool { IslandController.shared.enabled }

    /// App opened / came to the front: make sure Buddy is in the island (never overwrite what it shows now).
    func appearedInForeground() {
        if keepInIsland && !phase.isBusy && !IslandController.shared.isShowing {
            IslandController.shared.rest(message: answer.isEmpty ? "" : ReplyText.short(answer, limit: 120))
        }
    }

    // MARK: - asking

    func ask() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        let jpeg = picture.flatMap { BrainClient.jpeg(from: $0) }
        guard !text.isEmpty || jpeg != nil else {
            focusAsk = true
            return
        }
        question = ""
        ask(text.isEmpty ? "What is this, and what should I do here?" : text, jpeg: jpeg)
    }

    func ask(_ text: String, jpeg: Data?) {
        askTask?.cancel()
        speaker.stopSpeaking(at: .immediate)
        askedQuestion = text
        answer = ""
        notice = ""
        phase = jpeg == nil ? .thinking : .looking
        IslandController.shared.show(.init(phase: phase, question: ReplyText.short(text, limit: 60)), immediately: true)

        // Keep streaming for a while if the user swipes away to the app they asked about.
        holdBackgroundTime()
        askTask = Task {
            await self.run(text, jpeg: jpeg)
            self.endBackgroundTime()
        }
    }

    private func holdBackgroundTime() {
        endBackgroundTime()
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "BuddyAnswer") { [weak self] in
            MainActor.assumeIsolated {
                self?.endBackgroundTime()
            }
        }
    }

    private func endBackgroundTime() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    private func run(_ text: String, jpeg: Data?) async {
        let shortQuestion = ReplyText.short(text, limit: 60)
        do {
            let reply = try await BrainClient.shared.ask(text, jpeg: jpeg) { [weak self] soFar in
                guard let self, !soFar.isEmpty else { return }
                self.answer = soFar
                self.phase = .answering
                IslandController.shared.show(.init(phase: .answering, question: shortQuestion, answer: soFar))
            }
            let finalText = reply.isEmpty ? "Hmm, I'm not sure. Try asking another way?" : reply
            answer = finalText
            phase = .answer
            picture = nil
            IslandController.shared.finish(.init(phase: .answer, question: shortQuestion, answer: finalText))
            if readAloud { speak(finalText) }
        } catch is CancellationError {
            return
        } catch {
            if Task.isCancelled { return }
            let message = (error as? BrainError)?.message ?? StreamParser.friendlyError(status: nil, body: nil)
            answer = message
            phase = .trouble
            IslandController.shared.finish(.init(phase: .trouble, question: shortQuestion, answer: message))
        }
    }

    func askAboutLatestScreenshot(question text: String = "") {
        Task {
            do {
                let image = try await ScreenshotSource.latest()
                picture = image
                question = text
                ask()
            } catch {
                notice = error.localizedDescription
            }
        }
    }

    func pasteImage() {
        if let image = UIPasteboard.general.image {
            picture = image
        } else {
            notice = "There's no picture on the clipboard. Copy a screenshot first."
        }
    }

    func speak(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        speaker.speak(utterance)
    }

    func newConversation() {
        askTask?.cancel()
        BrainClient.shared.forget()
        askedQuestion = ""
        answer = ""
        phase = .ready
        picture = nil
        if keepInIsland { IslandController.shared.rest() }
    }

    // MARK: - links (buddy://ask, buddy://ask?q=...&screenshot=1)

    func handle(_ url: URL) {
        guard url.scheme == "buddy" else { return }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let text = items.first { $0.name == "q" }?.value ?? ""
        let wantsScreenshot = items.contains { $0.name == "screenshot" && $0.value != "0" }
        if wantsScreenshot {
            askAboutLatestScreenshot(question: text)
        } else if !text.isEmpty {
            question = text
            ask()
        } else {
            focusAsk = true
        }
    }

    // MARK: - updates (sideloaded apps can't update themselves: tell the user when a new build is out)

    func checkForUpdate() async {
        do {
            var request = URLRequest(url: BuddyConfig.manifestURL)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, _) = try await URLSession.shared.data(for: request)
            guard let manifest = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let ios = manifest["ios"] as? [String: Any],
                  let version = ios["version"] as? String else { return }
            updateVersion = Versions.isNewer(version, than: BrainClient.appVersion) ? version : nil
        } catch {
            updateVersion = nil
        }
    }

    // MARK: - CI helpers (launch arguments)

    /// -demo answer|thinking: fill the screen and the island with a sample, no network.
    func runDemo(_ kind: String) {
        let sampleQuestion = "Where do I change the revision in PLM?"
        let sampleAnswer = "Open the part, click Revise in the top toolbar, then pick the new revision letter " +
            "in the pop-up and press Save. The old revision stays in History."
        askedQuestion = sampleQuestion
        switch kind {
        case "thinking":
            phase = .thinking
            answer = ""
            IslandController.shared.show(.init(phase: .thinking, question: sampleQuestion), immediately: true)
        case "ready":
            phase = .ready
            askedQuestion = ""
            IslandController.shared.rest()
        default:
            phase = .answer
            answer = sampleAnswer
            IslandController.shared.show(.init(phase: .answer, question: sampleQuestion, answer: sampleAnswer),
                                         immediately: true)
        }
    }

    /// -selftest: real brain call + Live Activity; writes Documents/selftest.json for CI to read.
    func runSelfTest() async {
        var result: [String: Any] = ["version": BrainClient.appVersion,
                                     "activities_enabled": IslandController.shared.enabled]
        result["brain_health"] = await BrainClient.shared.healthy()
        ask("Reply with exactly these three words: Buddy is ready", jpeg: nil)
        _ = await askTask?.value
        result["phase"] = phase.rawValue
        result["answer"] = answer
        result["brain_ok"] = phase == .answer && !answer.isEmpty
        result["island_showing"] = IslandController.shared.isShowing
        if let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
           let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: folder.appendingPathComponent("selftest.json"))
        }
        NSLog("Buddy selftest: \(result)")
    }
}
