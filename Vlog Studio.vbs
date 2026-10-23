' Vlog Studio launcher for Windows: starts the local app (no console window) and opens it in your browser.
Option Explicit
Dim sh, fso, here, http, up, i, env
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

Function IsUp()
  Dim h
  IsUp = False
  On Error Resume Next
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1000, 1000, 1000, 1000
  h.Open "GET", "http://127.0.0.1:8765/api/meta", False
  h.Send
  If Err.Number = 0 Then
    If h.Status = 200 Then IsUp = True
  End If
  On Error GoTo 0
End Function

If Not IsUp() Then
  If Not fso.FileExists(here & "\.venv\Scripts\pythonw.exe") Then
    sh.Run "cmd /c """ & here & "\setup.bat""", 1, True
    If Not fso.FileExists(here & "\.venv\Scripts\pythonw.exe") Then WScript.Quit 1
  End If
  Set env = sh.Environment("Process")
  env("PYTHONPATH") = here
  env("PYTHONUTF8") = "1"
  sh.CurrentDirectory = here
  sh.Run """" & here & "\.venv\Scripts\pythonw.exe"" -m vlogstudio ui --no-browser --port 8765", 0, False
  For i = 1 To 40
    WScript.Sleep 500
    If IsUp() Then Exit For
  Next
End If

If IsUp() Then
  sh.Run "http://127.0.0.1:8765/"
Else
  MsgBox "Vlog Studio did not start. Details: " & here & "\vlogstudio_app.log", vbExclamation, "Vlog Studio"
End If
