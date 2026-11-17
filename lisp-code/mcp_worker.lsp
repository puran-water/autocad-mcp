;;; Resident File IPC worker candidate. No keyboard input between requests.
;;; Start explicitly in an idle document: (mcp-worker-run "<session token>").
;;; This owns the AutoCAD command processor until stopped or interrupted.
;;; mcp_dispatch.lsp (protocol 3) must already be loaded.

(defun mcp-worker-json-file (path json / fp tmp)
  ;; Heartbeats alternate slots: deleting one never removes the other snapshot.
  (setq tmp (strcat path ".tmp") fp (open tmp "w"))
  (if fp
    (progn (write-line json fp) (close fp)
      (if (findfile path) (vl-file-delete path))
      (vl-file-rename tmp path))))

(defun mcp-worker-drawing ()
  (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")))

(defun mcp-worker-normal-path (value)
  (strcase (vl-string-translate "\\" "/" value)))

(defun mcp-worker-heartbeat (phase reason / path json)
  (setq *mcp-worker-sequence* (1+ *mcp-worker-sequence*)
        path (strcat *mcp-worker-prefix* ".heartbeat-"
                     (itoa (rem *mcp-worker-sequence* 2)) ".json")
        json (strcat "{\"worker_protocol\":1,\"session\":\"" *mcp-worker-token*
                     "\",\"sequence\":" (itoa *mcp-worker-sequence*)
                     ",\"state\":\"" phase "\",\"reason\":\""
                     (mcp-escape-string reason) "\",\"drawing\":\""
                     (mcp-escape-string (mcp-worker-drawing))
                     "\",\"cmdactive\":" (itoa (getvar "CMDACTIVE"))
                     ",\"dbmod\":" (itoa (getvar "DBMOD"))
                     ",\"writestat\":" (itoa (getvar "WRITESTAT"))
                     ",\"build\":\"" (if *mcp-worker-build* *mcp-worker-build* "") "\""
                     ",\"request_id\":\"" *mcp-worker-request* "\"}"))
  (mcp-worker-json-file path json))

(defun mcp-worker-release (/ lock text)
  (setq lock (strcat *mcp-ipc-dir* "pcad-worker.lock.json"))
  (if (findfile lock)
    (progn
      (setq text (mcp-read-file-lines lock))
      (if (equal (mcp-json-get-string text "session") *mcp-worker-token*)
        (vl-file-delete lock)))))

(defun mcp-worker-run (token / *error* manifest text lock candidate started last-work
                           last-heartbeat idle-ms max-ms old-echo pending request
                           files ready result stop-reason now owner acquired)
  (defun *error* (message)
    (if acquired
      (progn
        (mcp-worker-heartbeat "stopped" (strcat "NATIVE_INTERRUPTION: " message))
        (mcp-worker-release)))
    (if old-echo (setvar "CMDECHO" old-echo))
    (setq *mcp-worker-running* nil)
    (princ (strcat "\nMCP worker stopped: " message)) (princ))
  (cond
    (*mcp-worker-running* (princ "\nMCP_WORKER_ALREADY_RUNNING"))
    ((not (and (= (type token) 'STR) (= (strlen token) 32)
               (= "" (vl-string-trim "0123456789abcdef" token))))
     (princ "\nMCP_WORKER_INVALID_SESSION"))
    ((not (equal *mcp-ipc-protocol* 3)) (princ "\nMCP_WORKER_DISPATCHER_REQUIRED"))
    (T
     (setq *mcp-worker-token* token
           *mcp-worker-prefix* (strcat *mcp-ipc-dir* "pcad-worker-" token)
           *mcp-worker-sequence* 0 *mcp-worker-request* ""
           manifest (strcat *mcp-worker-prefix* ".session.json")
           lock (strcat *mcp-ipc-dir* "pcad-worker.lock.json"))
     (if (findfile manifest) (setq text (mcp-read-file-lines manifest)))
     (cond
       ((or (findfile (strcat *mcp-worker-prefix* ".heartbeat-0.json"))
            (findfile (strcat *mcp-worker-prefix* ".heartbeat-1.json")))
        (princ "\nMCP_WORKER_SESSION_ALREADY_USED; prepare a fresh session"))
       ((not (and text (equal (mcp-json-get-string text "session") token)
                   (= (mcp-json-get-number text "worker_protocol") 1)))
        (mcp-worker-heartbeat "stopped" "INVALID_SESSION_MANIFEST"))
       ((/= (mcp-worker-normal-path (mcp-worker-drawing))
             (mcp-worker-normal-path (mcp-json-get-string text "drawing")))
        (mcp-worker-heartbeat "stopped" "DRAWING_IDENTITY_MISMATCH"))
       ((not (equal (mcp-json-get-string text "build") *mcp-worker-build*))
        (mcp-worker-heartbeat "stopped" "WORKER_BUILD_MISMATCH"))
       ((/= (getvar "WRITESTAT") 1)
        (mcp-worker-heartbeat "stopped" "DRAWING_READ_ONLY"))
       ((/= 0 (logand 1 (getvar "CMDACTIVE")))
        (mcp-worker-heartbeat "stopped" "AUTOCAD_COMMAND_ACTIVE"))
       ((or (findfile lock) (findfile (strcat *mcp-ipc-dir* "pcad-pending.json")))
        (mcp-worker-heartbeat "stopped" "EXISTING_WORKER_OR_PENDING_REQUEST"))
       (T
        (setq candidate (strcat *mcp-worker-prefix* ".lock-candidate.json"))
        (mcp-worker-json-file candidate text)
        (if (not (vl-file-rename candidate lock))
          (mcp-worker-heartbeat "stopped" "WORKER_LOCK_NOT_ACQUIRED")
          (progn
            (setq acquired T *mcp-worker-running* T
                  *mcp-worker-bound* (mcp-worker-normal-path (mcp-worker-drawing))
                  old-echo (getvar "CMDECHO")
                  idle-ms (* 1000 (mcp-json-get-number text "idle_timeout_s"))
                  max-ms (* 1000 (mcp-json-get-number text "max_duration_s"))
                  started (getvar "MILLISECS") last-work started last-heartbeat started)
            (setvar "CMDECHO" 0)
            (mcp-worker-heartbeat "idle" "")
            (while (not stop-reason)
              (setq now (getvar "MILLISECS"))
              (cond
                ((findfile (strcat *mcp-worker-prefix* ".stop.json"))
                 (setq stop-reason "STOP_REQUESTED"))
                ((/= *mcp-worker-bound* (mcp-worker-normal-path (mcp-worker-drawing)))
                 (setq stop-reason "DOCUMENT_CHANGED"))
                ((or (< now started) (> (- now started) max-ms))
                 (setq stop-reason "SESSION_DURATION_EXPIRED"))
                ((> (- now last-work) idle-ms) (setq stop-reason "SESSION_IDLE_EXPIRED"))
                (T
                 (setq pending (strcat *mcp-ipc-dir* "pcad-pending.json"))
                 ;; Python archives completed intent under its lease. The marker
                 ;; can disappear after findfile; a missing snapshot is ordinary
                 ;; cleanup, never an empty request to parse or replay.
                 (if (and (findfile pending)
                          (setq request (mcp-read-file-lines pending)))
                   (progn
                     (setq *mcp-worker-request* (mcp-json-get-string request "request_id"))
                     (cond
                       ((not (equal (mcp-json-get-string request "worker_session") token))
                        (setq stop-reason "REQUEST_SESSION_MISMATCH"))
                       ((not (and (= (type *mcp-worker-request*) 'STR)
                                  (= (strlen *mcp-worker-request*) 12)
                                  (= "" (vl-string-trim "0123456789abcdef" *mcp-worker-request*))))
                        (setq stop-reason "INVALID_REQUEST_ID"))
                       (T
                        (setq files (strcat *mcp-ipc-dir* "pcad-v3-" *mcp-worker-request*)
                              ready (strcat files ".ready.json"))
                        (if (findfile ready)
                          (cond
                            ((/= request (mcp-read-file-lines ready))
                             (setq stop-reason "READY_PENDING_MISMATCH"))
                            ((not (equal (mcp-json-get-string request "expected_drawing")
                                         *mcp-worker-bound*))
                             (setq stop-reason "REQUEST_DRAWING_MISMATCH"))
                            (T
                             (mcp-worker-heartbeat "running" "")
                             (mcp-dispatch-v3 *mcp-worker-request*)
                             (setq last-work (getvar "MILLISECS")
                                   result (strcat files ".result.json"))
                             (cond
                               ((findfile result)
                                (if (/= "true" (mcp-json-get-string (mcp-read-file-lines result) "ok"))
                                  (setq stop-reason "REQUEST_FAILED_RECONCILE_REQUIRED")))
                               ((findfile (strcat files ".claimed.json"))
                                (setq stop-reason "CLAIM_WITHOUT_RESULT")))
                             (setq *mcp-worker-request* "")
                             (if (not stop-reason) (mcp-worker-heartbeat "idle" "")))))))))
                 (if (> (- now last-heartbeat) 250)
                   (progn (mcp-worker-heartbeat "idle" "") (setq last-heartbeat now)))))
              (if (not stop-reason) (command-s "_.DELAY" 50)))
            (mcp-worker-heartbeat "stopped" stop-reason)
            (mcp-worker-release)
            (setvar "CMDECHO" old-echo)
            (setq *mcp-worker-running* nil)
            (princ (strcat "\nMCP worker stopped: " stop-reason))))))))
  (princ))

(princ "\nMCP queue worker loaded; explicit session start required.")
(princ)
