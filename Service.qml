import QtQuick
import Quickshell
import Quickshell.Io

// Owns every run of bin/omarchy-apod for the whole shell. Omarchy builds a bar
// per monitor, so nothing that fetches or writes may live on the widget, or
// each screen would download the picture again.
//
// The script does the real work and decides whether there is any: `update`
// runs a little after login and then hourly, and returns at once when today's
// picture is already showing. The widget's buttons go through run().
Item {
  id: root
  width: 0
  height: 0
  visible: false

  property var shell: null
  property var manifest: null
  property var pluginRegistry: null
  property var barWidgetRegistry: null
  property string omarchyPath: ""

  readonly property string script:
    Qt.resolvedUrl("bin/omarchy-apod").toString().replace(/^file:\/\//, "")

  // `omarchy-apod status`: the APOD entry plus mode, image, palette, themeActive, personalKey, page.
  property var status: ({})
  // The action in progress: "", "update", "latest", "random", "use" or "apply".
  property string busy: ""
  property string error: ""
  // What the running action is doing now (its latest progress message), and its download.
  property string stage: ""
  property real downloaded: 0
  property real downloadTotal: 0
  // Every message of the running action, for reason().
  property var messages: []

  property string pendingSettings: ""

  // Progress messages the script writes to stderr. Anything else is a reason for failure.
  readonly property var progress: /^(looking for a picture$|checking |downloading |fitting the picture|working out the theme|applying the theme$|theme built|APOD theme applied$|notified|no picture yet|settings saved$|HD image unavailable|trying the APOD website instead$|\S+ is a video; trying another$|\S+ is only \d+px; trying another$|no picture of \d+px or more found)/

  function begin(action) {
    root.busy = action
    root.stage = ""
    root.downloaded = 0
    root.downloadTotal = 0
    root.messages = []
  }

  // A stderr line of the running action: "progress DONE TOTAL" updates the download,
  // other progress messages become the stage.
  function readLine(line) {
    if (line.indexOf("omarchy-apod: ") !== 0) return
    var message = line.slice(14)
    var bytes = /^progress (\d+) (\d+)$/.exec(message)
    if (bytes) {
      root.downloaded = Number(bytes[1])
      root.downloadTotal = Number(bytes[2])
      return
    }
    root.messages.push(message)
    if (root.progress.test(message)) {
      root.stage = message
      root.downloaded = 0
      root.downloadTotal = 0
    }
  }

  function finish() {
    root.busy = ""
    root.stage = ""
    root.downloaded = 0
    root.downloadTotal = 0
    root.refresh()
  }

  // reason MESSAGES — the sentence to show for a failed run.
  function reason(lines) {
    // An API error that the website fallback dealt with is not the reason.
    var fallback = lines.lastIndexOf("trying the APOD website instead")
    var candidates = fallback >= 0 ? lines.slice(fallback + 1).concat(lines) : lines
    for (var i = 0; i < candidates.length; i++)
      if (!root.progress.test(candidates[i])) return candidates[i]
    return "something went wrong"
  }

  function run(action) {
    if (root.busy !== "" || ["latest", "random", "use", "apply"].indexOf(action) < 0) return false
    root.error = ""
    root.begin(action)
    // Long enough for a big picture on a slow connection; the widget shows the progress.
    actionProc.command = ["timeout", "--kill-after=5s", "5m", root.script, action]
    actionProc.running = true
    return true
  }

  function update() {
    if (root.busy !== "" || updater.running) return
    root.begin("update")
    updater.running = true
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
    else statusAgain.restart()
  }

  // The widget hands over its settings; the script keeps them, and rebuilds
  // the theme when a colour setting changed.
  function applySettings(values) {
    root.pendingSettings = JSON.stringify(values)
    settingsDebounce.restart()
  }

  function flushSettings() {
    if (root.pendingSettings === "" || configProc.running) return
    configProc.payload = root.pendingSettings
    root.pendingSettings = ""
    configProc.running = true
  }

  // Settings live in the widget's entry in shell.json. `omarchy bar set` writes them there
  // through the shell, which hot-reloads the file and hands the widget the new values;
  // the widget then passes them to the script like any other change. One at a time, in order.
  property var settingQueue: []

  function setSetting(key, value) {
    root.settingQueue = root.settingQueue.concat([[key, JSON.stringify(value)]])
    root.nextSetting()
  }

  function nextSetting() {
    if (settingProc.running || root.settingQueue.length === 0) return
    var item = root.settingQueue[0]
    root.settingQueue = root.settingQueue.slice(1)
    settingProc.command = ["omarchy-bar", "set", "io.github.smolamsk.apod", item[0], item[1], "--json"]
    settingProc.running = true
  }

  Process {
    id: settingProc
    stderr: StdioCollector { id: settingErr; waitForEnd: true }
    onExited: function(code) {
      if (code !== 0) root.error = "couldn't save the setting: " + String(settingErr.text || "").trim()
      root.nextSetting()
    }
  }

  // The NASA key is kept out of shell.json, which every user can read: `omarchy-apod key`
  // stores it privately. The script reads it from the clipboard itself, so it never passes
  // through argv or this process.
  property bool savingKey: false
  property string keyError: ""

  // how: "clipboard" or "clear". Bar popups get no keyboard, so the key comes from the clipboard.
  function saveKey(how) {
    if (keyProc.running || (how !== "clipboard" && how !== "clear")) return
    root.keyError = ""
    root.savingKey = true
    keyProc.command = [root.script, "key", "--" + how]
    keyProc.running = true
  }

  Process {
    id: keyProc
    stderr: StdioCollector { id: keyErr; waitForEnd: true }
    onExited: function(code) {
      if (code !== 0) {
        var lines = String(keyErr.text || "").split("\n").filter(function(l) { return l.indexOf("omarchy-apod: ") === 0 })
        root.keyError = lines.length ? lines[0].slice(14) : "couldn't save the key"
      }
      root.savingKey = false
      root.refresh()
    }
  }

  Process {
    id: actionProc
    environment: ({ APOD_PROGRESS: "1" })
    stderr: SplitParser { onRead: function(line) { root.readLine(line) } }
    onExited: function(code) {
      if (code === 124 || code === 137) root.error = "NASA took too long to answer; try again"
      else if (code !== 0) root.error = root.reason(root.messages)
      root.finish()
    }
  }

  Process {
    id: updater
    // Background runs may retry for a while when the network is down.
    command: ["timeout", "--kill-after=10s", "20m", root.script, "update"]
    environment: ({ APOD_PROGRESS: "1" })
    stderr: SplitParser { onRead: function(line) { root.readLine(line) } }
    onExited: function(code) {
      // Shown only until the next success; background failures are retried hourly.
      root.error = code === 0 ? "" : root.reason(root.messages)
      root.finish()
    }
  }

  Process {
    id: configProc
    property string payload: ""
    command: ["timeout", "--kill-after=5s", "90s", root.script, "configure"]
    // Over stdin, never argv: the API key would show in every user's process list.
    stdinEnabled: true
    onStarted: {
      write(payload + "\n")
      payload = ""
    }
    stderr: StdioCollector { id: configErr; waitForEnd: true }
    onExited: function(code) {
      if (code !== 0) root.error = root.reason(String(configErr.text || "").split("\n")
        .filter(function(l) { return l.indexOf("omarchy-apod: ") === 0 })
        .map(function(l) { return l.slice(14) }))
      root.refresh()
      root.flushSettings()
    }
  }

  Process {
    id: statusProc
    command: [root.script, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || "").trim() || "{}")
          if (parsed && typeof parsed === "object") root.status = parsed
        } catch (e) {
          console.warn("NASA APOD: unreadable status")
        }
      }
    }
  }

  Timer { id: statusAgain; interval: 300; onTriggered: root.refresh() }
  Timer { id: settingsDebounce; interval: 1000; onTriggered: root.flushSettings() }

  // Give the session a moment to settle (network, theme) before the first check.
  Timer {
    interval: 20000
    running: true
    onTriggered: root.update()
  }

  Timer {
    interval: 3600000
    running: true
    repeat: true
    onTriggered: root.update()
  }

  // Switching themes elsewhere changes whether "Use APOD theme" is offered.
  FileView {
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme.name"
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
  }

  Component.onCompleted: root.refresh()
}
