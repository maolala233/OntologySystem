import pytz
from datetime import datetime

# 1. 模拟数据库取出的 naive datetime（实际是 UTC 时间，但无时区信息）
utc_naive = datetime(2025, 3, 10, 6, 35, 22)   # 假设这是 UTC 时间 06:35:22

# 2. 目标时区：中国时区（东八区）
tz_china = pytz.timezone('Asia/Shanghai')

# 3. 错误做法：直接 astimezone(tz_china)
#    naive 对象被 Python 当作系统本地时区的时间
#    假设你的系统时区是 Asia/Shanghai，那么 utc_naive 被当作 06:35:22+08:00
#    再转换到 tz_china（也是东八区）数值不变，但标记了 +08:00
wrong_localized = utc_naive.astimezone(tz_china)
print("错误结果:", wrong_localized.isoformat())
# 输出：2025-03-10T06:35:22+08:00  （比正确时间少了 8 小时）

# 4. 正确做法：先将 naive 时间标记为 UTC，再转换到目标时区
utc_aware = pytz.utc.localize(utc_naive)          # 标记为 UTC
correct_localized = utc_aware.astimezone(tz_china) # 转换到东八区
print("正确结果:", correct_localized.isoformat())
# 输出：2025-03-10T14:35:22+08:00

# 5. 如果想用当前时间验证，可以替换 utc_naive 为当前 UTC 时间（但也是 naive）
now_utc_naive = datetime.utcnow()  # 注意：utcnow() 返回的是 naive UTC 时间
print("\n当前 UTC (naive):", now_utc_naive.isoformat())
wrong_now = now_utc_naive.astimezone(tz_china)
correct_now = pytz.utc.localize(now_utc_naive).astimezone(tz_china)
print("错误当前时间:", wrong_now.isoformat())
print("正确当前时间:", correct_now.isoformat())