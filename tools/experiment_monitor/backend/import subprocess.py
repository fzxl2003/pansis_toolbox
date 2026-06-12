import subprocess
import time
import smtplib
from email.mime.text import MIMEText
from email.header import Header

# ==========================
# 配置区域
# ==========================
HOST_NAME = "128服务器"
USERNAME = "pyh"

CHECK_INTERVAL = 100  # 每5分钟检测一次
ALERT_THRESHOLD = 3   # 连续3次异常才报警
MIN_DROP = 1          # 至少减少1个进程才算异常

SMTP_SERVER = "smtp.buaa.edu.cn"
SMTP_PORT = 465
SENDER_EMAIL = "21374282@buaa.edu.cn"
SENDER_PASSWORD = "I9vhI5CAQWMs85RW"
RECEIVER_EMAIL = "1449724101@qq.com"

# ==========================
# 获取服务器名称
# ==========================
def get_hostname():
    return HOST_NAME

# ==========================
# 获取 nvitop 输出
# ==========================
def get_nvitop_output():
    try:
        result = subprocess.run(
            ["nvitop", "--once"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )
        return result.stdout
    except Exception as e:
        print("Error running nvitop:", e)
        return ""

# ==========================
# 解析 pyh 用户进程
# ==========================
def parse_processes(output, username):
    lines = output.split("\n")
    processes = []

    for line in lines:
        if username in line:
            processes.append(line.strip())

    return processes

# ==========================
# 发送邮件
# ==========================
def send_email(subject, content):
    msg = MIMEText(content, "plain", "utf-8")

    # ⚠️ 必须带邮箱，否则可能被判 fake sender
    msg["From"] = f"GPU Monitor <{SENDER_EMAIL}>"
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = Header(subject, "utf-8")

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], msg.as_string())
        server.quit()
        print("邮件发送成功")
    except Exception as e:
        print("邮件发送失败:", e)

# ==========================
# 主监控逻辑
# ==========================
def monitor():
    hostname = get_hostname()

    baseline_count = None
    prev_processes = []
    decrease_count = 0

    print(f"开始监控用户 {USERNAME} 的 GPU 进程...")

    while True:
        output = get_nvitop_output()
        current_processes = parse_processes(output, USERNAME)
        current_count = len(current_processes)

        # ==========================
        # 初始化 baseline
        # ==========================
        if baseline_count is None:
            baseline_count = current_count
            prev_processes = current_processes
            print(f"初始化基准进程数: {baseline_count}")
            time.sleep(CHECK_INTERVAL)
            continue

        print(f"[状态] 当前: {current_count} | 基准: {baseline_count}")

        # ==========================
        # 判断是否异常（下降）
        # ==========================
        if baseline_count - current_count >= MIN_DROP:
            decrease_count += 1
            print(f"检测到下降 ({decrease_count}/{ALERT_THRESHOLD})")

        else:
            # 恢复或持平 → 重置
            if decrease_count > 0:
                print("状态恢复，计数清零")

            decrease_count = 0
            baseline_count = current_count  # 🔥更新基准

        # ==========================
        # 达到阈值 → 报警
        # ==========================
        if decrease_count >= ALERT_THRESHOLD:
            lost = set(prev_processes) - set(current_processes)

            subject = f"[告警] {hostname} 上 GPU 进程减少"
            content = f"""
服务器: {hostname}

用户: {USERNAME}

基准进程数: {baseline_count}
当前进程数: {current_count}

连续异常次数: {decrease_count}

减少的进程:
{chr(10).join(lost)}

当前进程列表:
{chr(10).join(current_processes)}
"""

            send_email(subject, content)

            # 🔥 关键：更新 baseline，避免重复报警
            baseline_count = current_count
            decrease_count = 0

        prev_processes = current_processes
        time.sleep(CHECK_INTERVAL)

# ==========================
# 启动
# ==========================
if __name__ == "__main__":
    # monitor()
    # lost = set(prev_processes) - set(current_processes)

    subject = " GPU 进程减少"
    content = """
服务器: 21321321312

"""

    send_email(subject, content)