import QtQuick 6.11
import Quickshell
import qs.Commons
import qs.Ui

// A shooting star in the bar. Click for the picture, its story, the colours
// the theme took from it, and Latest / Random. Middle-click shows another
// random picture. Everything that runs lives in Service.qml.
//
// Glyphs are \u escapes (U+F1741 star-shooting, U+F00F6 calendar, U+F049D
// shuffle, U+F03D8 palette, U+F03CC open-in-new) so the source survives
// editors that mangle private-use codepoints.
BarWidget {
  id: root
  moduleName: "io.github.smolamsk.apod"

  readonly property string apiKey: setting("apiKey", "")
  readonly property string colorMode: setting("colorMode", "Auto")
  readonly property string vividness: setting("vividness", "Balanced")
  readonly property bool notify: setting("notify", true)

  property var apod: null
  readonly property var status: apod ? apod.status : ({})
  readonly property string busy: apod ? apod.busy : ""
  readonly property string error: apod ? apod.error : ""
  readonly property string stage: apod ? apod.stage : ""
  readonly property real downloaded: apod ? apod.downloaded : 0
  readonly property real downloadTotal: apod ? apod.downloadTotal : 0
  readonly property bool downloading: busy !== "" && downloaded > 0
  readonly property bool loaded: !!status.title
  readonly property string mode: status.mode || "latest"
  readonly property var colors: status.palette && status.palette.colors ? status.palette.colors : ({})

  property bool popupOpen: false
  // open/close/opened let `omarchy-shell shell summon|hide|toggle io.github.smolamsk.apod`
  // drive the popup, e.g. from a keybinding.
  readonly property bool opened: popupOpen
  function open() {
    popupOpen = true
    if (apod) apod.refresh()
  }
  function close() { popupOpen = false }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function bindService() {
    if (!apod) {
      var host = bar && bar.shell ? bar.shell : null
      if (!host || typeof host.serviceFor !== "function") return
      apod = host.serviceFor("io.github.smolamsk.apod") || null
    }
    pushSettings()
  }

  function pushSettings() {
    if (!apod) return
    apod.applySettings({
      apiKey: apiKey,
      colorMode: colorMode,
      vividness: vividness,
      notify: notify
    })
  }

  function megabytes(bytes) {
    return (bytes / 1048576).toFixed(1)
  }

  function downloadText() {
    if (downloadTotal > 0)
      return megabytes(downloaded) + " of " + megabytes(downloadTotal) + " MB  ·  "
        + Math.floor(100 * downloaded / downloadTotal) + "%"
    return megabytes(downloaded) + " MB"
  }

  // What the script is doing now, in its own words, without the technical detail in brackets.
  function busyText() {
    if (stage !== "") {
      var text = stage.replace(/ \(.*\)$/, "")
      return text.charAt(0).toUpperCase() + text.slice(1) + "…"
    }
    if (busy === "update") return "Checking for a new picture…"
    if (busy === "latest") return "Fetching today's picture…"
    if (busy === "random") return "Finding a random picture…"
    if (busy === "use") return "Switching to the APOD theme…"
    if (busy === "apply") return "Rebuilding the theme…"
    return ""
  }

  onBarChanged: bindService()
  onApiKeyChanged: pushSettings()
  onColorModeChanged: pushSettings()
  onVividnessChanged: pushSettings()
  onNotifyChanged: pushSettings()

  Timer {
    interval: 200
    running: root.apod === null
    repeat: true
    onTriggered: root.bindService()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uDB85\uDF41"
    dimmed: root.busy !== "" && !root.loaded
    tooltipText: root.popupOpen ? "" : (root.busy !== "" ? root.busyText() + (root.downloading ? "\n" + root.downloadText() : "")
      : root.loaded ? "APOD: " + root.status.title : "NASA APOD")
    onPressed: function(b) {
      if (b === Qt.MiddleButton) {
        if (root.apod) root.apod.run("random")
      } else {
        root.popupOpen = !root.popupOpen
        if (root.popupOpen && root.apod) root.apod.refresh()
      }
    }
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(400))
    contentHeight: popup.fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      width: parent.width
      spacing: Style.space(10)

      // The picture as it is on the desktop. Click for the APOD page.
      Rectangle {
        width: parent.width
        height: Math.round(width * 9 / 16)
        radius: Style.spacing.labelGap
        color: Qt.rgba(0, 0, 0, 0.25)
        clip: true

        Image {
          anchors.fill: parent
          fillMode: Image.PreserveAspectCrop
          asynchronous: true
          cache: false
          sourceSize.width: 800
          source: root.status.image ? "file://" + root.status.image : ""
          visible: status === Image.Ready
        }

        Text {
          anchors.centerIn: parent
          visible: !root.status.image
          text: "\uDB85\uDF41"
          color: root.bar.foreground
          opacity: 0.5
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.displayLarge
        }

        MouseArea {
          anchors.fill: parent
          enabled: !!root.status.page
          cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
          onClicked: Qt.openUrlExternally(root.status.page)
        }
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        text: root.loaded ? root.status.title : (root.busy !== "" ? root.busyText() : "No picture yet")
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        wrapMode: Text.Wrap
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        visible: root.loaded
        text: (root.status.date || "") + (root.status.credit ? "  ·  © " + root.status.credit : "")
        color: root.bar.foreground
        opacity: 0.65
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }

      Flickable {
        id: story
        width: parent.width
        height: Math.min(explanation.implicitHeight, Style.space(150))
        visible: !!root.status.explanation
        contentHeight: explanation.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Text {
          id: explanation
          textFormat: Text.PlainText
          width: story.width
          text: root.status.explanation || ""
          color: root.bar.foreground
          opacity: 0.85
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
          lineHeight: 1.15
        }
      }

      // The colours the theme took from this picture.
      Row {
        id: swatches
        visible: root.colors.background !== undefined
        width: parent.width
        spacing: Style.space(4)
        readonly property var keys: ["background", "lighter_background", "selection", "accent", "red", "orange",
                                     "yellow", "green", "cyan", "blue", "magenta", "foreground"]

        Repeater {
          model: swatches.keys

          Rectangle {
            required property string modelData
            width: (swatches.width - swatches.spacing * (swatches.keys.length - 1)) / swatches.keys.length
            height: Style.space(14)
            radius: Style.space(3)
            color: root.colors[modelData] || "transparent"
            border.width: 1
            border.color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.2)
          }
        }
      }

      Row {
        spacing: Style.space(6)
        anchors.horizontalCenter: parent.horizontalCenter

        Button {
          iconText: "\uDB80\uDCF6"
          text: "Latest"
          foreground: root.bar.foreground
          selected: root.mode === "latest"
          // Once today's picture is showing there is nothing more to fetch.
          enabled: root.busy === "" && !(root.mode === "latest" && root.loaded)
          opacity: enabled || selected ? 1.0 : 0.4
          tooltipText: "NASA's newest picture, every day"
          onClicked: root.apod.run("latest")
        }

        Button {
          iconText: "\uDB81\uDC9D"
          text: root.mode === "random" ? "Shuffle" : "Random"
          foreground: root.bar.foreground
          selected: root.mode === "random"
          enabled: root.busy === ""
          opacity: enabled ? 1.0 : 0.4
          tooltipText: root.mode === "random" ? "Another random picture now"
                                              : "A random picture from the archive, every day"
          onClicked: root.apod.run("random")
        }

        Button {
          iconText: "\uDB80\uDFD8"
          text: "Use theme"
          foreground: root.bar.foreground
          visible: root.loaded && root.status.themeActive === false
          enabled: root.busy === ""
          opacity: enabled ? 1.0 : 0.4
          tooltipText: "Switch to the APOD theme"
          onClicked: root.apod.run("use")
        }

        Button {
          iconText: "\uDB80\uDFCC"
          foreground: root.bar.foreground
          visible: !!root.status.page
          tooltipText: "Open on science.nasa.gov"
          onClicked: Qt.openUrlExternally(root.status.page)
        }
      }

      // On days when APOD is a video, the newest photo stays; say so, with a link to the video.
      Text {
        textFormat: Text.PlainText
        width: parent.width
        visible: !!root.status.video && root.busy === ""
        text: root.status.video ? "Today's APOD is a video: " + root.status.video.title
          + ". Showing the most recent picture." : ""
        color: root.bar.foreground
        opacity: 0.75
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.underline: videoLink.containsMouse
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap

        MouseArea {
          id: videoLink
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: if (root.status.video) Qt.openUrlExternally(root.status.video.page)
        }
      }

      // Download progress. Without a size from the server, the bar sweeps instead of filling.
      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: root.downloading

        Rectangle {
          id: track
          property real sweep: 0
          width: parent.width
          height: Style.space(4)
          radius: height / 2
          color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g, root.bar.foreground.b, 0.15)
          clip: true

          NumberAnimation on sweep {
            from: 0
            to: 1
            duration: 1200
            loops: Animation.Infinite
            easing.type: Easing.InOutSine
            running: root.downloading && root.downloadTotal <= 0
          }

          Rectangle {
            height: parent.height
            radius: parent.radius
            color: Color.accent
            width: root.downloadTotal > 0
              ? Math.max(height, parent.width * Math.min(1, root.downloaded / root.downloadTotal))
              : parent.width / 4
            x: root.downloadTotal > 0 ? 0 : track.sweep * (parent.width - width)
            Behavior on width { NumberAnimation { duration: 400 } }
          }
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: root.downloadText()
          color: root.bar.foreground
          opacity: 0.65
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        visible: text !== ""
        text: root.error !== "" ? root.error
          : (root.busy !== "" && root.loaded) ? root.busyText() : ""
        color: root.error !== "" ? Color.urgent : root.bar.foreground
        opacity: root.error !== "" ? 1 : 0.65
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        visible: root.error.indexOf("DEMO_KEY") >= 0 || (root.error.indexOf("rate limit") >= 0 && root.status.personalKey === false)
        text: "Get a free key at api.nasa.gov, then save it with: ~/.config/omarchy/plugins/io.github.smolamsk.apod/bin/omarchy-apod key"
        color: root.bar.foreground
        opacity: 0.65
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
      }
    }
  }
}
