"""同源 iframe 刷作业闭环验证（结果写文件，避开控制台编码问题）。"""
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["ELH_ISMART_PROFILE"] = os.path.join(tempfile.gettempdir(), "el-shell3.json")
sys.path.insert(0, ".")

from core.platforms.ismart import COURSES_JS, ISmartClient, ISmartProfile  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "el_shell_report.txt")
lines = []


def log(*parts):
    lines.append(" ".join(str(p) for p in parts))


SHELL = """<html><head><title>SHELL</title></head><body><div id="index-iframe">
<iframe id="ifra" name="indexIframe" src="/courses.html" style="width:100%;height:400px"></iframe>
</div></body></html>"""

COURSES = """<html><head><title>COURSES</title></head><body>
<div class="course-item"><div class="title">WED-5-6</div><div class="teacher">主讲: XUHUI 老师</div>
<input type="button" value="进入课程" onclick="location.href='/course.html?c=0'"></div>
<div class="course-item"><div class="title">FRI-7-8</div><div class="teacher">主讲: LISI 老师</div>
<input type="button" value="进入课程" onclick="location.href='/course.html?c=1'"></div>
</body></html>"""

COURSE = """<html><head><title>COURSE</title></head><body>
<div class="task">Unit 1</div><button id="submit">提交</button>
<script>document.getElementById('submit').onclick=function(){
document.body.insertAdjacentHTML('beforeend','<div>提交成功</div>');};</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = COURSE if self.path.startswith("/course.") else (COURSES if self.path.startswith("/courses") else SHELL)
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/center"

p = ISmartProfile(port=9507)
p.browser_port = 9507
p.host_keyword = "127.0.0.1"
c = ISmartClient(p)

try:
    ok, msg = c.open_in_browser(url)
    log("attach:", ok)
    c._wait_content()
    log("top title:", c._eval_top("document.title"))
    log("content title:", c._eval("document.title"))
    log("content text:", ascii(str(c._eval("document.body.innerText"))[:60]))
    raw_courses = c._eval(COURSES_JS)
    log("raw courses_js:", ascii(raw_courses)[:300])

    ok, courses, message = c.get_courses()
    log("get_courses:", ok, ascii(courses)[:300], ascii(message))
    log("list_url:", c._list_url)
    log("outer_url:", c._outer_url)

    if courses:
        info_ok, info, _ = c.get_course_info(courses[0]["cid"])
        log("units:", info_ok, ascii(info)[:200])
        way1, way1_fail, _, _ = c.submit_course_progress(courses[0]["cid"], "cdp", "cdp", courses[0]["cid"], 100)
        log("submit:", way1, way1_fail)
        log("iframe after submit:", c._eval("location.href"))
        log("top after submit:", c._eval_top("location.href"))
except Exception as exc:
    log("EXCEPTION:", type(exc).__name__, ascii(str(exc))[:300])
finally:
    try:
        c.close()
    except Exception:
        pass
    srv.shutdown()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("REPORT WRITTEN", OUT)