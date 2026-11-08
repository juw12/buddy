import XCTest

final class BuddyTextTests: XCTestCase {
    func testStreamPieces() {
        XCTAssertEqual(StreamParser.piece(fromLine: #"data: {"choices":[{"delta":{"content":"Hi"}}]}"#), "Hi")
        XCTAssertEqual(StreamParser.piece(fromLine: #"data: {"choices":[{"message":{"content":"Whole"}}]}"#), "Whole")
        XCTAssertEqual(StreamParser.piece(fromLine: #"data: {"response":"workers-ai"}"#), "workers-ai")
        XCTAssertNil(StreamParser.piece(fromLine: "data: [DONE]"))
        XCTAssertNil(StreamParser.piece(fromLine: ": keep-alive"))
        XCTAssertNil(StreamParser.piece(fromLine: "data: {not json"))
        XCTAssertNil(StreamParser.piece(fromLine: #"data: {"choices":[{"delta":{}}]}"#))
    }

    func testFullReplyAndErrors() {
        let body = #"{"choices":[{"message":{"content":"Done."}}]}"#.data(using: .utf8)!
        XCTAssertEqual(StreamParser.fullReply(fromJSON: body), "Done.")
        let cap = #"{"error":{"code":"daily_cap","message":"x"}}"#.data(using: .utf8)!
        XCTAssertTrue(StreamParser.friendlyError(status: 429, body: cap).contains("daily limit"))
        XCTAssertTrue(StreamParser.friendlyError(status: 429, body: nil).contains("few seconds"))
        XCTAssertTrue(StreamParser.friendlyError(status: 502, body: nil).contains("502"))
        XCTAssertTrue(StreamParser.friendlyError(status: nil, body: nil).contains("connection"))
    }

    func testReplyCleaning() {
        XCTAssertEqual(ReplyText.clean("Click **Save** at the top. [POINT:500,40:Save]"), "Click Save at the top.")
        XCTAssertEqual(ReplyText.clean("Open   the\nmenu [POI"), "Open the menu")
        XCTAssertEqual(ReplyText.clean("  # Title\n> quote  "), "Title quote")
    }

    func testShortening() {
        XCTAssertEqual(ReplyText.short("short", limit: 20), "short")
        let long = "Open the part, click Revise in the top toolbar, then pick the new revision"
        let shortened = ReplyText.short(long, limit: 30)
        XCTAssertTrue(shortened.hasSuffix("\u{2026}"))
        XCTAssertLessThanOrEqual(shortened.count, 31)
        XCTAssertFalse(shortened.contains("Revi\u{2026}"), "cuts at a word boundary")
    }

    func testVersions() {
        XCTAssertTrue(Versions.isNewer("1.1.0", than: "1.0.9"))
        XCTAssertTrue(Versions.isNewer("v1.10.0", than: "1.9.9"))
        XCTAssertFalse(Versions.isNewer("1.1.0", than: "1.1.0"))
        XCTAssertFalse(Versions.isNewer("1.0", than: "1.0.1"))
        XCTAssertEqual(Versions.parts("2"), [2, 0, 0])
    }

    func testIslandTexts() {
        let resting = BuddyAttributes.ContentState(phase: .ready)
        XCTAssertEqual(resting.title, "Buddy")
        XCTAssertTrue(resting.body.contains("Tap to ask"))
        let busy = BuddyAttributes.ContentState(phase: .thinking, question: "Where is Save?")
        XCTAssertEqual(busy.title, "\u{201C}Where is Save?\u{201D}")
        XCTAssertTrue(busy.phase.isBusy)
        XCTAssertFalse(BuddyPhase.answer.isBusy)
        for phase in BuddyPhase.allCases {
            XCTAssertFalse(phase.status.isEmpty)
            XCTAssertFalse(phase.symbol.isEmpty)
        }
    }

    func testContentStateRoundTrip() throws {
        let state = BuddyAttributes.ContentState(phase: .answer, question: "q", answer: "a")
        let data = try JSONEncoder().encode(state)
        let back = try JSONDecoder().decode(BuddyAttributes.ContentState.self, from: data)
        XCTAssertEqual(back, state)
    }
}
