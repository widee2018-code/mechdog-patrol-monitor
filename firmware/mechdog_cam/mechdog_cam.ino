// MechDog ESP32-S3 카메라 모듈용 펌웨어 (Arduino).
// 카메라가 자기 Wi-Fi(AP)를 만드는 대신 기존 Wi-Fi에 접속하고, 같은 네트워크에서 영상을 제공한다.
//   http://<카메라-IP>/          미리보기 페이지
//   http://<카메라-IP>/capture   JPEG 스냅샷   (tools/camera_relay.py --url 에 사용)
//   http://<카메라-IP>:81/stream MJPEG 스트림
//   http://<카메라-IP>/status    JSON 상태
// Wi-Fi 접속에 실패하면 원래처럼 AP(MechDog-Cam-A, 192.168.4.1)로 열린다.
//
// 굽기 전에 반드시 원래 펌웨어를 백업할 것 (README의 "카메라 펌웨어 교체" 참고).
// secrets.h.example 을 secrets.h 로 복사하고 Wi-Fi 정보를 넣는다. secrets.h는 커밋하지 않는다.
#include "esp_camera.h"
#include "esp_http_server.h"
#include "img_converters.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include "secrets.h"   // WIFI_SSID, WIFI_PASS

const char* HOSTNAME = "mechdog-cam-a";   // http://mechdog-cam-a.local 로도 접속 가능
const char* AP_SSID = "MechDog-Cam-A";    // Wi-Fi 접속 실패 시 여는 AP
const char* AP_PASS = "mechdog123";       // 8자 이상. 데모용 비상 AP 비밀번호라 공개해도 됨 allow-secret

// 고정 IP를 쓰면 relay 주소가 바뀌지 않는다. 공유기의 대역에 맞게 고친다.
const bool USE_STATIC_IP = false;
IPAddress STATIC_IP(192, 168, 0, 21), GATEWAY(192, 168, 0, 1), SUBNET(255, 255, 255, 0);

// 카메라 핀 배치 후보. 순서대로 초기화를 시도해서 처음 성공한 것을 쓴다.
// 모듈 회로도나 원래 펌웨어 시리얼 로그로 실제 핀을 확인했다면 그것만 남겨 둔다.
struct CamPins {
  const char* name;
  int pwdn, reset, xclk, sda, scl;
  int y9, y8, y7, y6, y5, y4, y3, y2, vsync, href, pclk;
};
const CamPins PIN_MAPS[] = {
  // ESP32-S3-EYE / Freenove ESP32-S3-WROOM CAM 계열 (흔한 ESP32-S3 카메라 보드)
  {"ESP32-S3-EYE", -1, -1, 15, 4, 5, 16, 17, 18, 12, 10, 8, 9, 11, 6, 7, 13},
  // Seeed XIAO ESP32S3 Sense
  {"XIAO-S3-Sense", -1, -1, 10, 40, 39, 48, 11, 12, 14, 16, 18, 17, 15, 38, 47, 13},
};

// JPEG 한 장. fb가 있으면 프레임 버퍼를 빌린 것이고, 없으면 buf를 free 해야 한다.
// (Arduino가 함수 선언을 파일 위쪽에 자동으로 만들기 때문에 구조체는 함수들보다 앞에 둔다)
struct Jpeg { camera_fb_t* fb; uint8_t* buf; size_t len; };

const char* camName = "none";
const char* sensorName = "?";
bool sensorJpeg = true;   // false면 센서가 RGB565만 내므로 ESP32에서 JPEG로 압축한다
bool staMode = false;


esp_err_t tryInit(const CamPins& p, pixformat_t fmt) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_pwdn = p.pwdn;   c.pin_reset = p.reset;  c.pin_xclk = p.xclk;
  c.pin_sccb_sda = p.sda; c.pin_sccb_scl = p.scl;
  c.pin_d7 = p.y9; c.pin_d6 = p.y8; c.pin_d5 = p.y7; c.pin_d4 = p.y6;
  c.pin_d3 = p.y5; c.pin_d2 = p.y4; c.pin_d1 = p.y3; c.pin_d0 = p.y2;
  c.pin_vsync = p.vsync; c.pin_href = p.href; c.pin_pclk = p.pclk;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = fmt;
  c.grab_mode = CAMERA_GRAB_LATEST;   // 오래된 프레임 대신 항상 최신 프레임
  c.jpeg_quality = 12;
  c.fb_count = psramFound() ? 2 : 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  // 소프트웨어 JPEG 압축은 느리므로 RGB565일 때는 QVGA(320x240)로 둔다
  c.frame_size = (fmt == PIXFORMAT_JPEG && psramFound()) ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
  return esp_camera_init(&c);   // 실패하면 내부에서 정리하므로 바로 다시 시도할 수 있다
}

bool initCamera() {
  for (const CamPins& p : PIN_MAPS) {
    esp_err_t err = tryInit(p, PIXFORMAT_JPEG);
    if (err == ESP_ERR_NOT_SUPPORTED) {   // 센서는 찾았지만 JPEG를 지원하지 않음 (GC0308, GC2145 등)
      sensorJpeg = false;
      err = tryInit(p, PIXFORMAT_RGB565);
    }
    if (err == ESP_OK) {
      camName = p.name;
      sensor_t* s = esp_camera_sensor_get();
      camera_sensor_info_t* info = esp_camera_sensor_get_info(&s->id);
      if (info) sensorName = info->name;
      Serial.printf("카메라 초기화 성공: %s 핀 배치, 센서 %s (%s), PSRAM %s\n", p.name, sensorName,
                    sensorJpeg ? "JPEG" : "RGB565 -> 소프트웨어 JPEG", psramFound() ? "있음" : "없음");
      return true;
    }
    Serial.printf("카메라 초기화 실패: %s 핀 배치 (0x%x)\n", p.name, err);
  }
  return false;
}

// JPEG 한 장을 가져온다. 센서가 JPEG를 내면 프레임 버퍼를 그대로 쓰고, 아니면 압축한 사본을 만든다.
bool grabJpeg(Jpeg& j) {
  j.fb = esp_camera_fb_get();
  if (!j.fb) return false;
  if (sensorJpeg) {
    j.buf = j.fb->buf;
    j.len = j.fb->len;
    return true;
  }
  bool ok = frame2jpg(j.fb, 80, &j.buf, &j.len);   // 품질 0-100
  esp_camera_fb_return(j.fb);
  j.fb = NULL;
  return ok;
}

void releaseJpeg(Jpeg& j) {
  if (j.fb) esp_camera_fb_return(j.fb);
  else free(j.buf);
}


bool connectWifi() {
  WiFi.setHostname(HOSTNAME);   // core 3.x에서는 mode() 전에 불러야 적용된다
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);         // 절전을 끄면 스트림 지연이 크게 줄어든다
  if (USE_STATIC_IP) WiFi.config(STATIC_IP, GATEWAY, SUBNET, GATEWAY);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("Wi-Fi '%s' 접속 중", WIFI_SSID);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}


// ---- HTTP ----

static esp_err_t index_handler(httpd_req_t* req) {
  static const char page[] =
    "<!doctype html><meta name=viewport content='width=device-width'>"
    "<title>MechDog Cam</title><body style='margin:0;background:#111'>"
    "<img id=s style='width:100%'>"
    "<script>s.src='http://'+location.hostname+':81/stream'</script>";
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, page, sizeof(page) - 1);
}

static esp_err_t capture_handler(httpd_req_t* req) {
  Jpeg j;
  if (!grabJpeg(j)) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  esp_err_t res = httpd_resp_send(req, (const char*)j.buf, j.len);
  releaseJpeg(j);
  return res;
}

static esp_err_t status_handler(httpd_req_t* req) {
  char json[256];
  int n = snprintf(json, sizeof(json),
    "{\"mode\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"camera\":\"%s\",\"sensor\":\"%s\",\"hw_jpeg\":%s,\"psram\":%s,\"heap\":%u,\"uptime_s\":%lu}",
    staMode ? "sta" : "ap",
    (staMode ? WiFi.localIP() : WiFi.softAPIP()).toString().c_str(),
    staMode ? WiFi.RSSI() : 0, camName, sensorName, sensorJpeg ? "true" : "false", psramFound() ? "true" : "false",
    (unsigned)ESP.getFreeHeap(), millis() / 1000);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json, n);
}

#define BOUNDARY "mechdogframe"
static esp_err_t stream_handler(httpd_req_t* req) {
  static const char sep[] = "\r\n--" BOUNDARY "\r\n";
  char head[64];
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=" BOUNDARY);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  while (true) {
    Jpeg j;
    if (!grabJpeg(j)) return ESP_FAIL;
    int n = snprintf(head, sizeof(head), "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", j.len);
    esp_err_t res = httpd_resp_send_chunk(req, sep, sizeof(sep) - 1);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, head, n);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)j.buf, j.len);
    releaseJpeg(j);
    if (res != ESP_OK) return res;   // 클라이언트가 연결을 끊음
  }
}

// 스트림은 연결 하나를 계속 붙잡으므로 :81에 따로 띄워서 /capture가 막히지 않게 한다.
void startServers() {
  httpd_handle_t web = NULL, stream = NULL;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  httpd_uri_t index_uri   = {"/",        HTTP_GET, index_handler,   NULL};
  httpd_uri_t capture_uri = {"/capture", HTTP_GET, capture_handler, NULL};
  httpd_uri_t status_uri  = {"/status",  HTTP_GET, status_handler,  NULL};
  httpd_uri_t stream_uri  = {"/stream",  HTTP_GET, stream_handler,  NULL};
  if (httpd_start(&web, &cfg) == ESP_OK) {
    httpd_register_uri_handler(web, &index_uri);
    httpd_register_uri_handler(web, &capture_uri);
    httpd_register_uri_handler(web, &status_uri);
  }
  cfg.server_port = 81;
  cfg.ctrl_port += 1;
  if (httpd_start(&stream, &cfg) == ESP_OK) {
    httpd_register_uri_handler(stream, &stream_uri);
  }
}


void setup() {
  Serial.begin(115200);
  delay(500);
  if (!initCamera()) {
    Serial.println("카메라를 찾지 못함. PIN_MAPS에 이 모듈의 핀 배치를 추가하세요. 10초 후 재부팅.");
    delay(10000);
    ESP.restart();
  }

  staMode = connectWifi();
  IPAddress ip;
  if (staMode) {
    ip = WiFi.localIP();
    Serial.printf("Wi-Fi 연결됨, RSSI %d dBm\n", WiFi.RSSI());
  } else {
    Serial.println("Wi-Fi 연결 실패 (2.4 GHz인가요? 비밀번호가 맞나요?). AP 모드로 전환.");
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    ip = WiFi.softAPIP();
  }
  if (MDNS.begin(HOSTNAME)) MDNS.addService("http", "tcp", 80);

  startServers();
  Serial.printf("스냅샷: http://%s/capture\n", ip.toString().c_str());
  Serial.printf("스트림: http://%s:81/stream\n", ip.toString().c_str());
}


void loop() {
  // 공유기가 재부팅되는 등 연결이 끊기면 10초마다 다시 붙는다.
  if (staMode && WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi 끊김, 재접속 시도");
    WiFi.reconnect();
  }
  delay(10000);
}
