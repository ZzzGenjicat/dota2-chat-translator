Option Explicit

Dim shell, fso, appDir, runPy, command, runtime, exeName, candidate
Dim consoleMode, checkMode, argument, savedRuntime, runtimeConfig, runtimeReader, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
runPy = fso.BuildPath(appDir, "run.py")
shell.CurrentDirectory = appDir
consoleMode = False
checkMode = False
For Each argument In WScript.Arguments
    If LCase(argument) = "--console" Then consoleMode = True
    If LCase(argument) = "--print-runtime" Then checkMode = True
Next
exeName = "pythonw.exe"
If consoleMode Then exeName = "python.exe"

' Prefer the runtime travelling with this copy of the application.
runtime = ""
For Each candidate In Array(".runtime\python", ".venv\Scripts", ".")
    candidate = fso.BuildPath(fso.BuildPath(appDir, candidate), exeName)
    If fso.FileExists(candidate) Then
        runtime = fso.GetAbsolutePathName(candidate)
        Exit For
    End If
Next

' A recorded external runtime is a per-machine fallback, never the first choice.
If runtime = "" Then
    runtimeConfig = fso.BuildPath(appDir, "config\python_runtime.txt")
    savedRuntime = ""
    On Error Resume Next
    If fso.FileExists(runtimeConfig) Then
        If fso.GetFile(runtimeConfig).Size < 131072 Then
            Set runtimeReader = fso.OpenTextFile(runtimeConfig, 1, False, -1)
            savedRuntime = Trim(runtimeReader.ReadAll)
            runtimeReader.Close
        End If
    End If
    Err.Clear
    On Error GoTo 0
    If savedRuntime <> "" Then
        savedRuntime = Replace(shell.ExpandEnvironmentStrings(savedRuntime), "/", "\")
        If fso.GetDriveName(savedRuntime) = "" Then savedRuntime = fso.BuildPath(appDir, savedRuntime)
        candidate = fso.BuildPath(fso.GetParentFolderName(savedRuntime), exeName)
        If fso.FileExists(candidate) Then runtime = fso.GetAbsolutePathName(candidate)
    End If
End If

If runtime = "" Then
    ReportError "Offline Python runtime not found. Run install_offline.ps1 on this computer first."
End If
If checkMode Then
    WScript.StdOut.WriteLine runtime
    WScript.Quit 0
End If
If Not fso.FileExists(runPy) Then ReportError "run.py not found: " & runPy

command = """" & runtime & """ -X utf8 """ & runPy & """"
If consoleMode Then
    exitCode = shell.Run(command, 1, True)
    WScript.Quit exitCode
Else
    shell.Run command, 0, False
End If

Sub ReportError(message)
    If consoleMode Or checkMode Then
        WScript.StdOut.WriteLine message
    Else
        MsgBox message, vbCritical, "Dota 2 Translator"
    End If
    WScript.Quit 1
End Sub
