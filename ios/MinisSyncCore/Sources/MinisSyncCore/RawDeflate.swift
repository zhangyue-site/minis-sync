import Foundation
import zlib

public enum RawDeflate {
    /// ZIP uses raw DEFLATE, not a zlib header. Bound both input and output.
    public static func extract(input: FileHandle, compressedBytes: Int, expectedBytes: Int, output: FileHandle) throws {
        guard compressedBytes >= 0, expectedBytes >= 0 else { throw SyncFailure.invalid("压缩条目大小无效") }
        var stream = z_stream()
        guard inflateInit2_(&stream, -MAX_WBITS, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else { throw SyncFailure.invalid("无法初始化解压") }
        defer { inflateEnd(&stream) }
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 65536)
        defer { buffer.deallocate() }
        var remaining = compressedBytes, written = 0, ended = false
        while remaining > 0 && !ended {
            guard let data = try input.read(upToCount: min(65536, remaining)), !data.isEmpty else { throw SyncFailure.invalid("压缩条目截断") }
            remaining -= data.count
            try data.withUnsafeBytes { bytes in
                stream.next_in = UnsafeMutablePointer(mutating: bytes.bindMemory(to: UInt8.self).baseAddress)
                stream.avail_in = uInt(data.count)
                repeat {
                    stream.next_out = buffer; stream.avail_out = 65536
                    let code = inflate(&stream, Z_NO_FLUSH)
                    guard code == Z_OK || code == Z_STREAM_END else { throw SyncFailure.invalid("压缩条目损坏") }
                    let count = 65536 - Int(stream.avail_out); written += count
                    guard written <= expectedBytes else { throw SyncFailure.invalid("解压内容超过声明大小") }
                    if count > 0 { try output.write(contentsOf: Data(bytes: buffer, count: count)) }
                    if code == Z_STREAM_END {
                        ended = true
                        guard remaining == 0, stream.avail_in == 0 else { throw SyncFailure.invalid("压缩条目有多余字节") }
                        break
                    }
                } while stream.avail_in > 0 || stream.avail_out == 0
            }
        }
        guard ended, written == expectedBytes else { throw SyncFailure.invalid("压缩条目长度不符") }
    }
}
