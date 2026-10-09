param([string]$ConfigPath, [switch]$ShowControlKey)
$ErrorActionPreference = 'Stop'
# Run interactively on each trusted Windows account. Never paste tokens in chat.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class PrometheusHomeCredential {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  struct CREDENTIAL {
    public uint Flags, Type; public string TargetName, Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public uint CredentialBlobSize; public IntPtr CredentialBlob;
    public uint Persist, AttributeCount; public IntPtr Attributes;
    public string TargetAlias, UserName;
  }
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool CredWriteW(ref CREDENTIAL credential, uint flags);
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credential);
  [DllImport("advapi32.dll")] static extern void CredFree(IntPtr credential);
  public static void Write(string target, string secret) {
    byte[] bytes=Encoding.UTF8.GetBytes(secret); IntPtr blob=Marshal.AllocHGlobal(bytes.Length);
    try {
      Marshal.Copy(bytes,0,blob,bytes.Length);
      var c=new CREDENTIAL {Type=1,TargetName=target,CredentialBlobSize=(uint)bytes.Length,CredentialBlob=blob,Persist=2,UserName="Prometheus"};
      if(!CredWriteW(ref c,0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    } finally { for(int i=0;i<bytes.Length;i++) Marshal.WriteByte(blob,i,0); Marshal.FreeHGlobal(blob); Array.Clear(bytes,0,bytes.Length); }
  }
  public static string Read(string target) {
    IntPtr pointer; if(!CredReadW(target,1,0,out pointer)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    try { var c=(CREDENTIAL)Marshal.PtrToStructure(pointer,typeof(CREDENTIAL)); byte[] b=new byte[c.CredentialBlobSize]; Marshal.Copy(c.CredentialBlob,b,0,b.Length); return Encoding.UTF8.GetString(b); }
    finally { CredFree(pointer); }
  }
}
'@
if ($ShowControlKey) {
    Write-Host 'Paste this private control key only into Prometheus Smart home. Do not share it in chat:'
    Write-Host ([PrometheusHomeCredential]::Read('Prometheus/HomeControl'))
    exit 0
}
if (-not $ConfigPath -or -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw 'Provide -ConfigPath pointing to your reviewed SmartHome\config.json. Copy the example first.'
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if (-not $config.endpoint -or -not $config.devices) { throw 'Set the hub endpoint and named devices in your configuration first.' }
$random = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($random) } finally { $rng.Dispose() }
$controlKey = [Convert]::ToBase64String($random)
[PrometheusHomeCredential]::Write('Prometheus/HomeControl',$controlKey)
$sha = [Security.Cryptography.SHA256]::Create()
try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($controlKey))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
Write-Host 'Enter a Home Assistant long-lived access token for a dedicated user. It will be stored in this Windows account, not on the SSD.'
$secret = Read-Host -Prompt 'Home Assistant access token' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ($plain.Length -lt 20 -or $plain.Length -gt 2000 -or $plain -match '[^!-~]') { throw 'Unexpected token format.' }
    $record = @{endpoint=$config.endpoint.TrimEnd('/');control_key_sha256=$hash;token=$plain} | ConvertTo-Json -Compress
    [PrometheusHomeCredential]::Write('Prometheus/HomeAssistant',$record)
    $record=$null
} finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $plain=$null; $secret.Dispose() }
$config | Add-Member -NotePropertyName control_key_sha256 -NotePropertyValue $hash -Force
# Pairing never silently activates devices. Enable after reviewing entity actions.
$config | Add-Member -NotePropertyName enabled -NotePropertyValue $false -Force
$backup = $ConfigPath + '.before-pairing-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath $ConfigPath -Destination $backup -ErrorAction Stop
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
Write-Host 'Pairing saved. Configuration remains disabled. Set enabled to true after reviewing the devices and actions.'
Write-Host 'Private browser control key (paste only in Prometheus Smart home):'
Write-Host $controlKey
