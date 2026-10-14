' Zabbix Report Studio - silent desktop launcher (opens no console window)
' Use this for day-to-day use after the first setup (or create a shortcut to it).
' If dependencies are not installed yet, run run-app.bat first.
Dim sh, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw desktop.py", 0, False
