# Adds --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug" to all Chrome shortcuts
# Run once as Administrator (required for Chrome 136+)

$flags = '--remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"'

$shortcuts = @(
    "$env:USERPROFILE\Desktop\Google Chrome.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Google Chrome.lnk",
    "$env:PROGRAMDATA\Microsoft\Windows\Start Menu\Programs\Google Chrome.lnk",
    "$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Google Chrome.lnk"
)

$shell = New-Object -ComObject WScript.Shell

foreach ($path in $shortcuts) {
    if (Test-Path $path) {
        $shortcut = $shell.CreateShortcut($path)
        if ($shortcut.Arguments -notlike "*remote-debugging-port*") {
            $shortcut.Arguments = "$flags $($shortcut.Arguments)".Trim()
            $shortcut.Save()
            Write-Host "Updated: $path"
        } else {
            Write-Host "Already configured: $path"
        }
    }
}

Write-Host ""
Write-Host "Done! Chrome will now open with remote debugging port 9222."
Write-Host "Restart Chrome if it is currently open."
