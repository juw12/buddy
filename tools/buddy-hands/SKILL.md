---
name: buddy-hands
description: Click, type and look at windows on this Windows PC through Buddy (the desktop pet with the crew pointers), with your own named pointer shown on screen. Use when a task needs a real click or typed text in a desktop program (a button, tick box, tab, text box), when the user says "click it for me", "do it on screen", "use Buddy", "buddy hands", or when several agents have to work on screen at the same time.
---

# Buddy hands: really click and type on screen

Buddy (github.com/juw12/buddy, 1.9.0 or newer) does the click for you and shows your pointer flying to the
spot. Several agents can do this at the same time; each has its own pointer, name and colour.

## How to ask

Use the helper next to this file (it handles the quoting and prints Buddy's answer as one JSON line):

```powershell
$do = "$env:USERPROFILE\.config\opencode\skills\buddy-hands\buddy_do.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File $do -Agent <your agent name> -Do look  -Window "Notepad"
powershell -NoProfile -ExecutionPolicy Bypass -File $do -Agent <your agent name> -Do click -Window "Notepad" -Target "Save"
powershell -NoProfile -ExecutionPolicy Bypass -File $do -Agent <your agent name> -Do type  -Window "Save As" -Target "File name" -Text "notes"
powershell -NoProfile -ExecutionPolicy Bypass -File $do -Agent <your agent name> -Do click -X 900 -Y 500
```

- `-Agent` is your own name (for example `explorer`, `tester`). Keep the same name for the whole task: it is
  the name on your pointer. `-Team` is optional (for example `OpenBot`).
- `-Window` is a piece of the window's title. `-Target` is the name on the button, tick box, tab or text box.
- Without the helper: `Buddy.exe --crew "{\"agent\": \"tester\", \"do\": \"click\", \"target\": \"Save\",
  \"window\": \"Notepad\"}" --out answer.json`, then read `answer.json`.

## The way to work

1. **Look first.** `-Do look` lists what Buddy can see in the window: `{"name": "Save", "kind": "button",
   "x": 412, "y": 300}`. Use those names as `-Target`.
2. **Click or type by name.** That is pressed directly: the user's mouse does not move, and other agents can
   work at the same time.
3. **Read the answer.** `{"ok": true, "did": "Clicked Save", "how": "direct"}` means it was done. With
   `"ok": false`, `did` says why; do not repeat the same request blindly.
4. **Look again** after a click that changes the window (a dialog opens, a tab changes).
5. Use `-X`/`-Y` (the numbers from `look`) only when a thing has no name. That uses the real mouse for a
   moment, one agent at a time.

## What Buddy will not do (do not try to get around it)

- Nothing until the user said yes (Buddy asks them once). The answer then says agents may not click.
- Nothing that is covered by another window or not on screen. Ask the user to bring the window to the front.
- Never a window's Close, Minimize, Maximize or Exit button, and never Buddy's own windows.
- A name such as Delete, Send, Remove, Reset, Don't save or Pay: Buddy asks the user first. If the answer is
  no, stop and tell the user; do not look for another way to press it.
- Typed text is not shown in the bubble. Still, do not type passwords or keys through this.

Exit codes: 0 done, 3 not done, 1 Buddy is not running, 2 Buddy.exe not found (start Buddy).
