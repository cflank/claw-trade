#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cJSON.h"
#include "ss_define.h"
#include "ss_error.h"
#include "ss_lm_control.h"

typedef enum {
    STATUS_NOT_ACTIVATED = 0,
    STATUS_ACTIVATED,
    STATUS_GRACE_PERIOD,
    STATUS_EXPIRED,
    STATUS_REVOKED,
    STATUS_RUNTIME_UNAVAILABLE,
} normalized_status_t;

static const char *status_name(normalized_status_t status) {
    switch (status) {
        case STATUS_ACTIVATED:
            return "activated";
        case STATUS_GRACE_PERIOD:
            return "grace_period";
        case STATUS_EXPIRED:
            return "expired";
        case STATUS_REVOKED:
            return "revoked";
        case STATUS_RUNTIME_UNAVAILABLE:
            return "runtime_unavailable";
        case STATUS_NOT_ACTIVATED:
        default:
            return "not_activated";
    }
}

static int status_allows_features(normalized_status_t status) {
    return status == STATUS_ACTIVATED || status == STATUS_GRACE_PERIOD;
}

static normalized_status_t map_license_status(int raw_status) {
    switch (raw_status) {
        case 0:
            return STATUS_ACTIVATED;
        case 1:
            return STATUS_GRACE_PERIOD;
        case 2:
            return STATUS_EXPIRED;
        case 4:
            return STATUS_REVOKED;
        default:
            return STATUS_NOT_ACTIVATED;
    }
}

static int wanted_license_id(void) {
    const char *value = getenv("VIRBOX_LICENSE_ID");
    char *end = NULL;
    long parsed;

    if (value == NULL || value[0] == '\0') {
        return -1;
    }
    parsed = strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed <= 0 || parsed > 2147483647L) {
        return -2;
    }
    return (int)parsed;
}

static int json_int(cJSON *object, const char *key, int fallback) {
    cJSON *item = cJSON_GetObjectItem(object, key);
    if (item != NULL && item->type == cJSON_Number) {
        return item->valueint;
    }
    return fallback;
}

static const char *json_string(cJSON *object, const char *key) {
    cJSON *item = cJSON_GetObjectItem(object, key);
    if (item != NULL && item->type == cJSON_String && item->valuestring != NULL) {
        return item->valuestring;
    }
    return NULL;
}

static void add_null_or_string(cJSON *object, const char *key, const char *value) {
    if (value == NULL || value[0] == '\0') {
        cJSON_AddNullToObject(object, key);
    } else {
        cJSON_AddStringToObject(object, key, value);
    }
}

static void unix_time_to_iso(int timestamp, char *buffer, size_t buffer_size) {
    time_t value = (time_t)timestamp;
    struct tm tm_value;

    if (timestamp <= 0 || buffer_size == 0 || gmtime_r(&value, &tm_value) == NULL) {
        if (buffer_size > 0) {
            buffer[0] = '\0';
        }
        return;
    }
    strftime(buffer, buffer_size, "%Y-%m-%dT%H:%M:%SZ", &tm_value);
}

static void suffix_from_license_key(const char *license_key, char *buffer, size_t buffer_size) {
    size_t len;
    size_t out = 0;

    if (buffer_size == 0) {
        return;
    }
    buffer[0] = '\0';
    if (license_key == NULL) {
        return;
    }

    len = strlen(license_key);
    while (len > 0 && out + 1 < buffer_size) {
        char c = license_key[--len];
        if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) {
            buffer[out++] = c;
            if (out == 4) {
                break;
            }
        }
    }
    for (size_t i = 0; i < out / 2; ++i) {
        char tmp = buffer[i];
        buffer[i] = buffer[out - i - 1];
        buffer[out - i - 1] = tmp;
    }
    buffer[out] = '\0';
}

static void maybe_read_license_suffix(void *ipc, char *buffer, size_t buffer_size) {
    SS_CHAR *offline_desc = NULL;
    SS_UINT32 ret = slm_ctrl_get_offline_desc(ipc, &offline_desc);
    cJSON *root = NULL;

    if (ret != SS_OK || offline_desc == NULL) {
        return;
    }
    root = cJSON_Parse(offline_desc);
    if (root != NULL && root->type == cJSON_Array) {
        int size = cJSON_GetArraySize(root);
        for (int index = 0; index < size; ++index) {
            cJSON *item = cJSON_GetArrayItem(root, index);
            const char *license_key = item == NULL ? NULL : json_string(item, "license_key");
            if (license_key != NULL) {
                suffix_from_license_key(license_key, buffer, buffer_size);
                break;
            }
        }
    }
    cJSON_Delete(root);
    slm_ctrl_free(offline_desc);
}

static void print_result(
    normalized_status_t status,
    int raw_status,
    const char *expires_at,
    const char *license_suffix,
    const char *error_code,
    const char *error_message
) {
    cJSON *root = cJSON_CreateObject();
    cJSON *features = cJSON_CreateArray();
    char raw_status_text[32];
    char *printed = NULL;

    if (status_allows_features(status)) {
        cJSON_AddItemToArray(features, cJSON_CreateString("report"));
        cJSON_AddItemToArray(features, cJSON_CreateString("data_refresh"));
    }

    if (raw_status >= 0) {
        snprintf(raw_status_text, sizeof(raw_status_text), "%d", raw_status);
        cJSON_AddStringToObject(root, "raw_status", raw_status_text);
    } else {
        cJSON_AddNullToObject(root, "raw_status");
    }
    cJSON_AddStringToObject(root, "normalized_status", status_name(status));
    cJSON_AddItemToObject(root, "features", features);
    add_null_or_string(root, "expires_at", expires_at);
    cJSON_AddNullToObject(root, "grace_until");
    cJSON_AddNullToObject(root, "device_id_hash");
    add_null_or_string(root, "license_suffix", license_suffix);
    add_null_or_string(root, "error_code", error_code);
    add_null_or_string(root, "error_message", error_message);

    printed = cJSON_PrintUnformatted(root);
    if (printed != NULL) {
        puts(printed);
        free(printed);
    }
    cJSON_Delete(root);
}

static normalized_status_t better_status(normalized_status_t current, normalized_status_t next) {
    if (next == STATUS_ACTIVATED) {
        return next;
    }
    if (current == STATUS_ACTIVATED) {
        return current;
    }
    if (next == STATUS_GRACE_PERIOD) {
        return next;
    }
    if (current == STATUS_GRACE_PERIOD) {
        return current;
    }
    if (next == STATUS_EXPIRED || next == STATUS_REVOKED) {
        return next;
    }
    return current;
}

static void consider_license(
    cJSON *license,
    int wanted_id,
    int *matched,
    int *best_raw_status,
    char *best_expires_at,
    size_t best_expires_at_size,
    normalized_status_t *best_status
) {
    int license_id;
    int raw_status = -1;
    int end_time;
    normalized_status_t mapped;
    cJSON *status;

    if (license == NULL) {
        return;
    }

    license_id = json_int(license, "license_id", -1);
    if (wanted_id > 0 && license_id != wanted_id) {
        return;
    }

    status = cJSON_GetObjectItem(license, "lic_status");
    if (status != NULL && status->type == cJSON_Object) {
        raw_status = json_int(status, "status", -1);
    }
    if (raw_status < 0) {
        return;
    }

    end_time = json_int(license, "end_time", 0);
    mapped = map_license_status(raw_status);
    *matched = 1;
    *best_status = better_status(*best_status, mapped);
    if (*best_raw_status < 0 || *best_status == mapped) {
        *best_raw_status = raw_status;
        unix_time_to_iso(end_time, best_expires_at, best_expires_at_size);
    }
}

static void scan_descriptions(
    void *ipc,
    cJSON *descriptions,
    int wanted_id,
    int *matched,
    int *best_raw_status,
    char *best_expires_at,
    size_t best_expires_at_size,
    normalized_status_t *best_status
) {
    if (descriptions == NULL || descriptions->type != cJSON_Array) {
        return;
    }

    for (int device_index = 0; device_index < cJSON_GetArraySize(descriptions); ++device_index) {
        cJSON *device = cJSON_GetArrayItem(descriptions, device_index);
        char *device_desc = NULL;
        SS_CHAR *licenses_text = NULL;
        cJSON *licenses = NULL;
        SS_UINT32 ret;

        if (device == NULL) {
            continue;
        }
        device_desc = cJSON_PrintUnformatted(device);
        if (device_desc == NULL) {
            continue;
        }

        ret = slm_ctrl_read_brief_license_context(ipc, JSON, device_desc, &licenses_text);
        free(device_desc);
        if (ret != SS_OK || licenses_text == NULL) {
            continue;
        }

        licenses = cJSON_Parse(licenses_text);
        if (licenses != NULL && licenses->type == cJSON_Array) {
            for (int license_index = 0; license_index < cJSON_GetArraySize(licenses); ++license_index) {
                consider_license(
                    cJSON_GetArrayItem(licenses, license_index),
                    wanted_id,
                    matched,
                    best_raw_status,
                    best_expires_at,
                    best_expires_at_size,
                    best_status
                );
            }
        }

        cJSON_Delete(licenses);
        slm_ctrl_free(licenses_text);
    }
}

int main(void) {
    void *ipc = NULL;
    SS_CHAR *devices_text = NULL;
    SS_CHAR *offline_text = NULL;
    cJSON *devices = NULL;
    SS_UINT32 ret;
    int wanted_id = wanted_license_id();
    int matched = 0;
    int best_raw_status = -1;
    char best_expires_at[32] = "";
    char license_suffix[16] = "";
    normalized_status_t best_status = STATUS_NOT_ACTIVATED;

    if (wanted_id == -2) {
        print_result(
            STATUS_RUNTIME_UNAVAILABLE,
            -1,
            NULL,
            NULL,
            "invalid_license_id",
            "VIRBOX_LICENSE_ID must be a positive integer"
        );
        return 2;
    }

    ret = slm_ctrl_client_open(&ipc);
    if (ret != SS_OK) {
        char message[80];
        snprintf(message, sizeof(message), "slm_ctrl_client_open failed: 0x%08X", ret);
        print_result(STATUS_RUNTIME_UNAVAILABLE, -1, NULL, NULL, "virbox_ipc_error", message);
        return 2;
    }

    maybe_read_license_suffix(ipc, license_suffix, sizeof(license_suffix));

    ret = slm_ctrl_get_all_description(ipc, JSON, &devices_text);
    if (ret != SS_OK || devices_text == NULL) {
        char message[96];
        snprintf(message, sizeof(message), "slm_ctrl_get_all_description failed: 0x%08X", ret);
        slm_ctrl_client_close(ipc);
        print_result(STATUS_RUNTIME_UNAVAILABLE, -1, NULL, license_suffix, "virbox_description_error", message);
        return 2;
    }

    devices = cJSON_Parse(devices_text);
    if (devices == NULL || devices->type != cJSON_Array) {
        cJSON_Delete(devices);
        slm_ctrl_free(devices_text);
        slm_ctrl_client_close(ipc);
        print_result(STATUS_RUNTIME_UNAVAILABLE, -1, NULL, license_suffix, "virbox_json_error", "device description is not a JSON array");
        return 2;
    }

    scan_descriptions(
        ipc,
        devices,
        wanted_id,
        &matched,
        &best_raw_status,
        best_expires_at,
        sizeof(best_expires_at),
        &best_status
    );

    if (!matched && slm_ctrl_get_offline_desc(ipc, &offline_text) == SS_OK && offline_text != NULL) {
        cJSON *offline_devices = cJSON_Parse(offline_text);
        scan_descriptions(
            ipc,
            offline_devices,
            wanted_id,
            &matched,
            &best_raw_status,
            best_expires_at,
            sizeof(best_expires_at),
            &best_status
        );
        cJSON_Delete(offline_devices);
        slm_ctrl_free(offline_text);
    }

    cJSON_Delete(devices);
    slm_ctrl_free(devices_text);
    slm_ctrl_client_close(ipc);

    if (!matched) {
        print_result(STATUS_NOT_ACTIVATED, -1, NULL, license_suffix, NULL, NULL);
        return 0;
    }

    print_result(best_status, best_raw_status, best_expires_at, license_suffix, NULL, NULL);
    return 0;
}
