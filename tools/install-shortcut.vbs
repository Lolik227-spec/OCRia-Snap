' Создаёт ярлык OCRia Snap (с иконкой). Вызывается из install-autostart.cmd.
' Аргументы: 1) pythonw.exe  2) ocria_snap.pyw  3) ocria-snap.ico  4) startup|desktop
Set sh = CreateObject("WScript.Shell")
pyw    = WScript.Arguments(0)
script = WScript.Arguments(1)
icon   = WScript.Arguments(2)
where  = LCase(WScript.Arguments(3))

If where = "desktop" Then
    folder = sh.SpecialFolders("Desktop")
Else
    folder = sh.SpecialFolders("Startup")
End If

Set lnk = sh.CreateShortcut(folder & "\OCRia Snap.lnk")
lnk.TargetPath = pyw
lnk.Arguments = """" & script & """"
lnk.WorkingDirectory = Left(script, InStrRev(script, "\") - 1)
lnk.IconLocation = icon
lnk.Description = "OCRia Snap — скриншот в текст"
lnk.WindowStyle = 7
lnk.Save
WScript.Echo folder & "\OCRia Snap.lnk"
