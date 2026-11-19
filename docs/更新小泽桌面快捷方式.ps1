$ErrorActionPreference = 'Stop'
$projectPath = 'C:\Users\ZhuanZ\Documents\Codex\wechat-acheng'
$desktopFolder = 'C:\Users\ZhuanZ\Desktop\奇思妙想'
$legacyNames = @('启动阿澄微信.lnk', '启动小泽微信私聊.lnk')
$backupPath = Join-Path $projectPath 'docs\shortcut-backup'
$shortcutShell = New-Object -ComObject WScript.Shell
$cmdPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
$explorerPath = Join-Path $env:SystemRoot 'explorer.exe'
$notepadPath = Join-Path $env:SystemRoot 'System32\notepad.exe'
$pythonWindowIcon = 'C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\pythonw.exe,0'
if (!(Test-Path -LiteralPath $desktopFolder -PathType Container)) { throw 'Desktop folder missing' }
$entries = @(
    @{ Name='启动小泽微信.lnk'; Target=$cmdPath; Args='/d /c ""' + (Join-Path $projectPath '启动小泽微信.cmd') + '""'; Description='复用原阿澄完整流程：小泽群聊、私聊、双会话与偶尔插话'; Icon=$pythonWindowIcon },
    @{ Name='小泽本地试聊.lnk'; Target=$cmdPath; Args='/d /c ""' + (Join-Path $projectPath '启动小泽本地试聊.cmd') + '""'; Description='小泽本地连续聊天，不依赖微信'; Icon=$pythonWindowIcon },
    @{ Name='小泽项目文件夹.lnk'; Target=$explorerPath; Args='"' + $projectPath + '"'; Description='打开现有小泽项目目录'; Icon=$explorerPath + ',0' },
    @{ Name='小泽使用说明.lnk'; Target=$notepadPath; Args='"' + (Join-Path $projectPath 'docs\小泽快速使用说明.txt') + '"'; Description='小泽快捷入口与微信私聊白话使用说明'; Icon=$notepadPath + ',0' }
)
foreach ($legacyName in $legacyNames) {
    $legacyPath = Join-Path $desktopFolder $legacyName
    if (Test-Path -LiteralPath $legacyPath) {
        New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
        $backupFile = Join-Path $backupPath ([IO.Path]::GetFileNameWithoutExtension($legacyName) + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.lnk')
        Copy-Item -LiteralPath $legacyPath -Destination $backupFile
    }
}
foreach ($entry in $entries) {
    if (!(Test-Path -LiteralPath $entry.Target)) { throw 'Shortcut executable missing' }
    $linkPath = Join-Path $desktopFolder $entry.Name
    $link = $shortcutShell.CreateShortcut($linkPath)
    $link.TargetPath = $entry.Target
    $link.Arguments = $entry.Args
    $link.WorkingDirectory = $projectPath
    $link.Description = $entry.Description
    $link.IconLocation = $entry.Icon
    $link.WindowStyle = 1
    $link.Save()
    $verified = $shortcutShell.CreateShortcut($linkPath)
    if ($verified.TargetPath -ne $entry.Target -or $verified.Arguments -ne $entry.Args -or $verified.WorkingDirectory -ne $projectPath) { throw 'Shortcut verification failed' }
    Write-Output ($entry.Name + ' : verified')
}
# Only replace the explicitly named legacy shortcut after all new links verify.
foreach ($legacyName in $legacyNames) {
    $legacyPath = Join-Path $desktopFolder $legacyName
    if (Test-Path -LiteralPath $legacyPath) { Remove-Item -LiteralPath $legacyPath }
}
Write-Output 'Replaced legacy shortcut; no applications launched or runtime data changed.'
