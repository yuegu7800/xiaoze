$ErrorActionPreference = 'Stop'
$shortcutDirectory = 'C:\Users\ZhuanZ\Desktop\奇思妙想'
$shortcutPath = Join-Path $shortcutDirectory '启动阿澄微信.lnk'
$launcherPath = Join-Path $PSScriptRoot 'start-acheng.cmd'
$commandPath = Join-Path $env:SystemRoot 'System32\cmd.exe'

if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw '找不到阿澄启动脚本。'
}
if (-not (Test-Path -LiteralPath $shortcutDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $shortcutDirectory | Out-Null
}
if (Test-Path -LiteralPath $shortcutPath) {
    throw '快捷方式已存在，请先核对现有文件，未覆盖。'
}

$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $commandPath
$shortcut.Arguments = '/d /k "' + $launcherPath + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = '阿澄微信聊天机器人：群聊、私聊、人物记忆和朋友圈草稿'
$shortcut.WindowStyle = 1
$iconPath = 'D:\wecaht\Weixin\Weixin.exe'
if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
    $shortcut.IconLocation = $iconPath + ',0'
}
$shortcut.Save()

$savedShortcut = $shortcutShell.CreateShortcut($shortcutPath)
if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf) -or
    $savedShortcut.TargetPath -ne $commandPath -or
    $savedShortcut.WorkingDirectory -ne $PSScriptRoot -or
    $savedShortcut.Arguments -ne $shortcut.Arguments) {
    throw '快捷方式保存后的属性核对失败。'
}
[pscustomobject]@{
    Shortcut = $shortcutPath
    Target = $savedShortcut.TargetPath
    Arguments = $savedShortcut.Arguments
    WorkingDirectory = $savedShortcut.WorkingDirectory
} | Format-List
