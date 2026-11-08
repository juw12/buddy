import PhotosUI
import SwiftUI

struct ContentView: View {
    @ObservedObject var model: BuddyModel
    @FocusState private var typing: Bool
    @State private var pickedItem: PhotosPickerItem?
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    if let version = model.updateVersion {
                        updateBanner(version)
                    }
                    answerCard
                    quickActions
                    if !model.notice.isEmpty {
                        Text(model.notice)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 4)
                    }
                    tips
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) { composer }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Buddy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { model.newConversation() } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel("New conversation")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Settings")
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView(model: model)
            }
        }
        .tint(BuddyFace.orange)
        .onChange(of: model.focusAsk) { _, wanted in
            if wanted {
                typing = true
                model.focusAsk = false
            }
        }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                    model.picture = image
                }
                pickedItem = nil
            }
        }
    }

    // MARK: - pieces

    private var header: some View {
        HStack(spacing: 12) {
            BuddyFace(size: 46, phase: model.phase)
            VStack(alignment: .leading, spacing: 2) {
                Text(model.phase.isBusy ? model.phase.status + "\u{2026}" : "Hi! What can I help with?")
                    .font(.headline)
                Text(model.islandAvailable
                     ? (model.keepInIsland ? "I'm in your Dynamic Island - tap it any time." : "I show up in the Dynamic Island while I answer.")
                     : "Turn on Live Activities for Buddy in Settings to keep me in the Dynamic Island.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func updateBanner(_ version: String) -> some View {
        Link(destination: BuddyConfig.installPage) {
            HStack {
                Image(systemName: "arrow.down.circle.fill")
                Text("Buddy \(version) is out - tap for the update")
                    .font(.subheadline.weight(.semibold))
                Spacer()
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 14).fill(BuddyFace.orange.opacity(0.18)))
        }
    }

    private var answerCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !model.askedQuestion.isEmpty {
                Text("\u{201C}\(model.askedQuestion)\u{201D}")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            if model.answer.isEmpty {
                Text(model.phase.isBusy ? "\(model.phase.status)\u{2026}" :
                        "Ask anything, or take a screenshot of the app you're stuck in and tap \u{201C}My last screenshot\u{201D}.")
                    .foregroundStyle(.secondary)
            } else {
                Text(model.answer)
                    .font(.body)
                    .textSelection(.enabled)
            }
            if !model.answer.isEmpty && !model.phase.isBusy {
                HStack {
                    Button { model.speak(model.answer) } label: {
                        Label("Read aloud", systemImage: "speaker.wave.2")
                    }
                    Spacer()
                    ShareLink(item: model.answer) {
                        Label("Share", systemImage: "square.and.arrow.up")
                    }
                }
                .font(.footnote)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 18).fill(Color(.secondarySystemGroupedBackground)))
    }

    private var quickActions: some View {
        HStack(spacing: 10) {
            Button { model.askAboutLatestScreenshot(question: model.question) } label: {
                Label("My last screenshot", systemImage: "camera.viewfinder")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            PhotosPicker(selection: $pickedItem, matching: .images) {
                Label("Photo", systemImage: "photo")
            }
            .buttonStyle(.bordered)
            Button { model.pasteImage() } label: {
                Label("Paste", systemImage: "doc.on.clipboard")
            }
            .buttonStyle(.bordered)
        }
        .labelStyle(.titleAndIcon)
        .font(.subheadline)
    }

    private var tips: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Buddy everywhere").font(.subheadline.weight(.semibold))
            tip("hand.tap", "Tap the Dynamic Island to ask. Long-press it to read the whole answer.")
            tip("iphone.gen3.radiowaves.left.and.right", "Back Tap / Action button: run the \u{201C}Ask Buddy about my screen\u{201D} shortcut (Settings below) and the answer appears in the island - you never leave your app.")
            tip("square.grid.2x2", "Add the Ask Buddy widget to your Home Screen or Lock Screen.")
            tip("mic", "Say \u{201C}Ask Buddy\u{201D} to Siri, or use the keyboard's microphone to talk.")
        }
        .font(.footnote)
        .foregroundStyle(.secondary)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 18).fill(Color(.secondarySystemGroupedBackground)))
    }

    private func tip(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol).frame(width: 20).foregroundStyle(BuddyFace.orange)
            Text(text)
        }
    }

    private var composer: some View {
        VStack(spacing: 8) {
            if let picture = model.picture {
                HStack(spacing: 8) {
                    Image(uiImage: picture)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Text("Buddy will look at this picture").font(.footnote).foregroundStyle(.secondary)
                    Spacer()
                    Button { model.picture = nil } label: { Image(systemName: "xmark.circle.fill") }
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Remove picture")
                }
            }
            HStack(spacing: 8) {
                TextField("Ask Buddy anything\u{2026}", text: $model.question)
                    .focused($typing)
                    .submitLabel(.send)
                    .onSubmit { model.ask() }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Capsule().fill(Color(.secondarySystemGroupedBackground)))
                Button { model.ask() } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 34))
                }
                .accessibilityLabel("Ask")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }
}

struct SettingsView: View {
    @ObservedObject var model: BuddyModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("Keep Buddy in the Dynamic Island", isOn: $model.keepInIsland)
                    Toggle("Read answers aloud", isOn: $model.readAloud)
                } footer: {
                    Text("Buddy rests in the island (or on the Lock Screen) so it is one tap away. iOS shows a Live Activity for up to 8 hours; opening Buddy starts a new one.")
                }
                Section("Ask about any screen - without leaving it") {
                    Text("1. Open Shortcuts and make a new shortcut.")
                    Text("2. Add \u{201C}Take Screenshot\u{201D}, then \u{201C}Ask Buddy\u{201D} and set Picture to the screenshot.")
                    Text("3. Settings > Accessibility > Touch > Back Tap > Double Tap: pick that shortcut (or put it on the Action button).")
                    Text("Now double-tap the back of your iPhone anywhere: Buddy looks at the screen and answers in the Dynamic Island.")
                        .foregroundStyle(.secondary)
                }
                Section("About") {
                    LabeledContent("Version", value: BrainClient.appVersion)
                    if let version = model.updateVersion {
                        Link("Get Buddy \(version)", destination: BuddyConfig.installPage)
                    } else {
                        Text("You have the latest version.").foregroundStyle(.secondary)
                    }
                    Link("Buddy for Windows, Mac and the web", destination: URL(string: "https://juw12.github.io/buddy/")!)
                    Link("Send feedback", destination: URL(string: "https://github.com/juw12/buddy/issues/new?template=feedback.yml")!)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
