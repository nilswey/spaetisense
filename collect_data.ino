#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include "VEML6070.h"
#include "Adafruit_HDC1000.h"
#include "esp_camera.h"
#include "esp_task_wdt.h"
#include <Sp_tiSense2.0_inferencing.h>

// Configure Wifi connection
const char* ssid     = "Galaxy A54 5G A364";
const char* password = "12345678"; 

// Initialize Open Sense Map (Staging) Data
const char* SENSEBOX_ID = "vhrqgsr7wo7nmzjx69o0xlqp";
const char* API_KEY     = "N9KXsjEmphco7Hf9eumA6dFYBS0h5Eap-GdBKpbpPhw";
const char* SERVER      = "api.staging.opensensemap.org";

// Sensor IDs
const char* ID_TEMP   = "55d81f21e7f8181bea8ff2a6";
const char* ID_HUM    = "9b9eb5d697fdf3d9779bbd97";
const char* ID_UV     = "2730238af2942b9f94c24f40";
const char* ID_PEOPLE = "811247b6dab866791a9f873e";
const char* ID_SOUND  = "920ce623fe3dc611150496fd";

// Hardware
#define PIN_QWIIC_SDA 2
#define PIN_QWIIC_SCL 1

Adafruit_HDC1000 hdc = Adafruit_HDC1000();
camera_fb_t *fb_global = NULL;
volatile int persons_found = 0;
volatile bool inference_done = false;

// Sending Data to OpenSense Map
void postToOpenSenseMap(float temp, float hum, uint16_t uv, float sound, int people) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WiFi] not connected.");
        return;
    }
    WiFiClientSecure client;
    client.setInsecure();

    if (client.connect(SERVER, 443)) {
        // Create CSV Body
        String body = "";
        body += String(ID_TEMP) + "," + String(temp, 2) + "\n";
        body += String(ID_HUM) + "," + String(hum, 2) + "\n";
        body += String(ID_UV) + "," + String(uv) + "\n";
        body += String(ID_SOUND) + "," + String(sound, 2) + "\n";
        body += String(ID_PEOPLE) + "," + String(people);

        // Send HTTP
        client.print("POST /boxes/" + String(SENSEBOX_ID) + "/data HTTP/1.1\r\n");
        client.print("Host: " + String(SERVER) + "\r\n");
        client.print("Authorization: " + String(API_KEY) + "\r\n");
        client.print("Content-Type: text/csv\r\n");
        client.print("Content-Length: " + String(body.length()) + "\r\n");
        client.print("Connection: close\r\n\r\n");
        client.print(body);

        unsigned long timeout = millis();
        while (client.connected() && millis() - timeout < 2000) {
            if (client.available()) {
                String line = client.readStringUntil('\r');
                if (line.indexOf("201") != -1) {
                    Serial.println("[osem] Daten erfolgreich übertragen (201).");
                }
            }
        }
        client.stop();
    } else {
        Serial.println("[osem] Verbindungsfehler zum Server.");
    }
}

// Camera Callback
static int ei_camera_get_data(size_t offset, size_t length, float *out_ptr) {
    size_t pixel_offset = offset;
    size_t out_ptr_ix = 0;
    while (length--) {
        uint8_t gray = fb_global->buf[pixel_offset++];
        out_ptr[out_ptr_ix++] = (float)((gray << 16) | (gray << 8) | gray);
    }
    return 0;
}

// Inferenz Task
void inferenceTask(void *param) {
    while (true) {
        fb_global = esp_camera_fb_get();
        if (!fb_global) {
            delay(1000);
            continue;
        }

        signal_t signal;
        signal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
        signal.get_data = &ei_camera_get_data;

        ei_impulse_result_t result = { 0 };
        if (run_classifier(&signal, &result, false) == EI_IMPULSE_OK) {
            int count = 0;
            for (size_t ix = 0; ix < result.bounding_boxes_count; ix++) {
                if (result.bounding_boxes[ix].value >= 0.2) count++;
            }
            persons_found = count;
            inference_done = true;
        }

        esp_camera_fb_return(fb_global);
        delay(10);
    }
}

const int soundPin = 14; // Pindefinition for Gravity Sound Level

void setup() {
    Serial.begin(115200);
    pinMode(soundPin, INPUT);

    // Connect WIFI
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.println("\nWiFi OK.");

    // Hardware
    esp_task_wdt_deinit();
    Wire.begin(PIN_QWIIC_SDA, PIN_QWIIC_SCL);
    VEML.begin();
    if (!hdc.begin()) Serial.println("HDC fehlt!");

    // Config HDF3M-811 XR Camera
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = 11;
    config.pin_d1 = 9;
    config.pin_d2 = 8;
    config.pin_d3 = 10;
    config.pin_d4 = 12;
    config.pin_d5 = 18;
    config.pin_d6 = 17;
    config.pin_d7 = 16;
    config.pin_xclk = 15;
    config.pin_pclk = 13;
    config.pin_vsync = 6;
    config.pin_href = 7;
    config.pin_sccb_sda = 4;
    config.pin_sccb_scl = 5;
    config.pin_pwdn = 46;
    config.pin_reset = -1;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_GRAYSCALE;
    config.frame_size = FRAMESIZE_240X240;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    if (esp_camera_init(&config) != ESP_OK) {
        Serial.println("Camera error!");
        while(1);
    }

    xTaskCreate(inferenceTask, "inference", 32768, NULL, 1, NULL);
}

void loop() {
    if (inference_done) {
        float t = hdc.readTemperature();
        float h = hdc.readHumidity();
        uint16_t uv = VEML.read_uvs_step();


        // Average calculation
        int raw = analogRead(soundPin);
        float spl =(raw*(3.3/4095))*50;
        Serial.print("Average Sound Level: ");
        Serial.println(spl);
        
        // Return for Serial Monitor
        Serial.print("Persons:"); 
        Serial.println(persons_found);

        postToOpenSenseMap(t, h, uv, spl, persons_found);

        inference_done = false;
    }
}