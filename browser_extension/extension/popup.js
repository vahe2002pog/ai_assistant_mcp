chrome.runtime.sendMessage({ type: "get_status" }, function(response) {
  var el = document.getElementById("status");
  if (response && response.connected) {
    el.textContent = "Connected to MCP";
    el.className = "on";
  } else {
    el.textContent = "Disconnected";
    el.className = "off";
  }
});
