// 时间工具：统一解析/格式化后端返回的时间字段（BUG-014 修复）
// 后端 API 返回的时间戳是不带时区后缀的 naive UTC 字符串（如 "2026-07-26T16:45:04.123722"），
// 直接 dayjs(value) 会被当成浏览器本地时间解析，北京时区下全站显示会偏早 8 小时。
// 这里统一按 UTC 解析后转为本地时间；若字符串已带 Z 或 ±hh:mm 偏移量
// （兼容后端将来改为返回 aware ISO 字符串），则交给 dayjs 原生解析，两种口径都能得到正确时刻。
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc.js'

dayjs.extend(utc)

// 结尾带 Z 或 ±hh:mm / ±hhmm 视为已携带时区信息
const TZ_SUFFIX_RE = /(?:Z|[+-]\d{2}:?\d{2})$/i

// 解析后端时间字段，返回本地时区的 dayjs 对象；空值返回 null
export function parseServerTime(value) {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'string' && !TZ_SUFFIX_RE.test(value)) {
    // naive 字符串按 UTC 解析，再转回本地时区
    return dayjs.utc(value).local()
  }
  return dayjs(value)
}

// 格式化后端时间字段为本地时间字符串；空值或非法值返回 fallback
export function formatServerTime(value, format, fallback = '') {
  const parsed = parseServerTime(value)
  return parsed && parsed.isValid() ? parsed.format(format) : fallback
}
