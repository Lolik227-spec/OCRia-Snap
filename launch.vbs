' OCRia Snap — запуск без окна консоли
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & dir & "\start-ocria-snap.cmd""", 0, False
