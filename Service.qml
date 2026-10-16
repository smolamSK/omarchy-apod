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

  property string pendingSettings: ""

  // Progress lines the script writes to stderr. Anything else is a reason for failure.
  readonly property var progress: /^(downloading |rendering |theme built|APOD theme applied|notified|no picture yet|settings saved|HD image unavailable|trying the APOD website instead$|.* is under \d+px, trying another$)/

  // reason TEXT — the sentence to show for a failed run's stderr.
  function reason(text) {
    var lines = String(text || "").split("\n")
      .filter(function(l) { return l.indexOf("omarchy-apod: ") === 0 })
      .map(function(l) { return l.slice(14) })
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
    root.busy = action
    actionProc.command = ["timeout", "--kill-after=5s", "90s", root.script, action]
    actionProc.running = true
    return true
  }

  function update() {
    if (root.busy !== "" || updater.running) return
    root.busy = "update"
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

  Process {
    id: actionProc
    stderr: StdioCollector { id: actionErr; waitForEnd: true }
    onExited: function(code) {
      if (code === 124 || code === 137) root.error = "NASA took too long to answer; try again"
      else if (code !== 0) root.error = root.reason(actionErr.text)
      root.busy = ""
      root.refresh()
    }
  }

  Process {
    id: updater
    // Background runs may retry for a while when the network is down.
    command: ["timeout", "--kill-after=10s", "20m", root.script, "update"]
    stderr: StdioCollector { id: updateErr; waitForEnd: true }
    onExited: function(code) {
      // Shown only until the next success; background failures are retried hourly.
      root.error = code === 0 ? "" : root.reason(updateErr.text)
      root.busy = ""
      root.refresh()
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
      if (code !== 0) root.error = root.reason(configErr.text)
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
