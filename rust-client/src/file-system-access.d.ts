// File System Access API surface we use that isn't in the standard TS DOM lib
// (Chromium-only). Interface names match lib.dom's, so these merge in the extra members.
interface FileSystemHandlePermissionDescriptor {
  mode?: 'read' | 'readwrite'
}
interface FileSystemHandle {
  queryPermission?(d?: FileSystemHandlePermissionDescriptor): Promise<PermissionState>
  requestPermission?(d?: FileSystemHandlePermissionDescriptor): Promise<PermissionState>
}
interface FilePickerAcceptType {
  description?: string
  accept: Record<string, string[]>
}
interface OpenFilePickerOptions {
  multiple?: boolean
  excludeAcceptAllOption?: boolean
  types?: FilePickerAcceptType[]
}
interface Window {
  showOpenFilePicker?(options?: OpenFilePickerOptions): Promise<FileSystemFileHandle[]>
}
interface DataTransferItem {
  getAsFileSystemHandle?(): Promise<FileSystemHandle | null>
}
