import Foundation
import UIKit

struct BrainError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Talks to Buddy's free brain (the same Cloudflare Worker the Windows, Mac and web islands use).
/// OpenAI-compatible chat completions with streaming; a photo goes along as a data: URL.
final class BrainClient {
    static let shared = BrainClient()

    /// Short memory so follow-up questions work ("and then?").
    private(set) var history: [[String: Any]] = []
    private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 60
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration)
    }

    func forget() {
        history.removeAll()
    }

    /// Ask a question (optionally about a JPEG). `onText` gets the cleaned reply so far, on the main actor.
    func ask(_ question: String, jpeg: Data?, onText: @escaping @MainActor (String) -> Void) async throws -> String {
        var request = URLRequest(url: BuddyConfig.brainURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Buddy/\(Self.appVersion) (iPhone)", forHTTPHeaderField: "User-Agent")

        let userContent: Any
        if let jpeg {
            userContent = [
                ["type": "text", "text": question],
                ["type": "image_url", "image_url": ["url": "data:image/jpeg;base64," + jpeg.base64EncodedString()]],
            ]
        } else {
            userContent = question
        }
        var messages: [[String: Any]] = [["role": "system", "content": BuddyConfig.systemPrompt]]
        messages += history.suffix(2 * BuddyConfig.historyTurns)
        messages.append(["role": "user", "content": userContent])
        let body: [String: Any] = ["model": BuddyConfig.model, "messages": messages, "stream": true, "max_tokens": 400]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BrainError(message: StreamParser.friendlyError(status: nil, body: nil))
        }
        if http.statusCode != 200 {
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count > 4000 { break }
            }
            throw BrainError(message: StreamParser.friendlyError(status: http.statusCode, body: data))
        }

        var reply = ""
        let contentType = http.value(forHTTPHeaderField: "Content-Type") ?? ""
        if contentType.contains("application/json") {
            var data = Data()
            for try await byte in bytes { data.append(byte) }
            reply = StreamParser.fullReply(fromJSON: data) ?? ""
            let shown = ReplyText.clean(reply)
            await onText(shown)
        } else {
            var lastShown = Date.distantPast
            for try await line in bytes.lines {
                guard let piece = StreamParser.piece(fromLine: line) else { continue }
                reply += piece
                if Date().timeIntervalSince(lastShown) > 0.25 {
                    lastShown = Date()
                    let shown = ReplyText.clean(reply)
                    await onText(shown)
                }
            }
        }
        let finalText = ReplyText.clean(reply)
        await onText(finalText)
        history.append(["role": "user", "content": question])
        history.append(["role": "assistant", "content": finalText])
        if history.count > 4 * BuddyConfig.historyTurns {
            history.removeFirst(history.count - 2 * BuddyConfig.historyTurns)
        }
        return finalText
    }

    /// Is the brain up? Used by the self-test and the settings sheet.
    func healthy() async -> Bool {
        do {
            let (_, response) = try await session.data(from: BuddyConfig.healthURL)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    /// Shrink a picture to at most 1280 px on the long side and encode it as JPEG.
    static func jpeg(from image: UIImage) -> Data? {
        let longest = max(image.size.width, image.size.height)
        let scale = longest > BuddyConfig.maxImageSide ? BuddyConfig.maxImageSide / longest : 1
        let size = CGSize(width: floor(image.size.width * scale), height: floor(image.size.height * scale))
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let resized = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        return resized.jpegData(compressionQuality: 0.8)
    }
}
