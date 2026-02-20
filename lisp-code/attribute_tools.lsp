;;; Attribute Tools for AutoCAD MCP
;;; Tools for handling block attributes in P&ID and other drawings
;;; Compatible with AutoCAD LT 2024+

;; Insert block without attributes (simpler approach for P&ID symbols)
(defun c:insert-block-simple (block-path x y scale rotation / block-name slash-pos dot-pos old-attreq)
  "Insert a block without attribute prompting"
  ;; Extract block name from path without using VL functions (for LT compatibility)
  ;; Find last slash
  (setq slash-pos 0)
  (setq i 1)
  (while (<= i (strlen block-path))
    (if (= (substr block-path i 1) "/")
      (setq slash-pos i))
    (setq i (1+ i))
  )
  
  ;; Extract filename after last slash
  (if (> slash-pos 0)
    (setq block-name (substr block-path (1+ slash-pos)))
    (setq block-name block-path)
  )
  
  ;; Remove .dwg extension if present
  (setq dot-pos (- (strlen block-name) 3))
  (if (= (strcase (substr block-name dot-pos)) ".DWG")
    (setq block-name (substr block-name 1 (1- dot-pos)))
  )
  
  ;; Check if block already exists
  (if (not (tblsearch "BLOCK" block-name))
    (progn
      ;; Load block definition first with cancel
      (command "_.-INSERT" block-path nil)
      (princ (strcat "\nLoaded block definition: " block-name))
    )
  )
  
  ;; Insert block WITHOUT attribute prompting
  (setq old-attreq (getvar "ATTREQ"))
  (setvar "ATTREQ" 0)  ; Disable attribute prompts - use defaults
  
  ;; Insert the block
  (command "_.-INSERT" block-name (list x y 0.0) scale scale rotation)
  
  ;; Restore ATTREQ
  (setvar "ATTREQ" old-attreq)
  
  (princ (strcat "\nInserted " block-name))
)

;; Update block attributes using entity modification (proper method for AutoCAD LT)
(defun c:update-block-attribute (block-ent tag-name new-value / ent ent-data attr-data new-attr-data)
  "Update a specific attribute value on a block entity using entget/entmod"
  ;; Start with the block entity and look for its attributes
  (setq ent (entnext block-ent))  ; Get first subentity (usually first attribute)
  
  ;; Loop through all subentities looking for attributes
  (while ent
    (setq ent-data (entget ent))
    
    ;; Check if this is an attribute entity
    (if (= (cdr (assoc 0 ent-data)) "ATTRIB")
      (progn
        ;; Check if this is the attribute tag we're looking for
        (if (= (strcase (cdr (assoc 2 ent-data))) (strcase tag-name))
          (progn
            ;; Update the attribute value (DXF code 1)
            (setq new-attr-data (subst (cons 1 new-value) (assoc 1 ent-data) ent-data))
            (entmod new-attr-data)
            (entupd ent)  ; Update the display
            (princ (strcat "\nUpdated attribute " tag-name " to: " new-value))
          )
        )
      )
    )
    ;; Move to next subentity
    (setq ent (entnext ent))
  )
)

;; Insert P&ID equipment with correct CTO block attributes
(defun c:insert-pid-equipment (category symbol-name x y scale rotation equipment-no equipment-type manufacturer model-no line-no capacity / block-path block-ent)
  "Insert P&ID equipment with proper CTO block attributes"
  ;; Build full path
  (setq block-path (strcat "C:/PIDv4-CTO/" category "/" symbol-name ".dwg"))
  
  ;; Insert block without attribute prompting
  (c:insert-block-simple block-path x y scale rotation)
  
  ;; Get the last inserted entity (the block we just inserted)
  (setq block-ent (entlast))
  
  ;; Update attributes based on equipment category
  (cond
    ;; PUMPS-BLOWERS: EQUIPMENT-TYPE, MANUFACTURER, MODEL-NO, EQUIPMENT-NO, LINE-NO
    ((= (strcase category) "PUMPS-BLOWERS")
     (if equipment-type (c:update-block-attribute block-ent "EQUIPMENT-TYPE" equipment-type))
     (if manufacturer (c:update-block-attribute block-ent "MANUFACTURER" manufacturer))
     (if model-no (c:update-block-attribute block-ent "MODEL-NO" model-no))
     (if equipment-no (c:update-block-attribute block-ent "EQUIPMENT-NO" equipment-no))
     (if line-no (c:update-block-attribute block-ent "LINE-NO" line-no)))
    
    ;; TANKS: EQUIPMENT-TYPE, MANUFACTURER, MODEL-NO, EQUIPMENT-NO, CAPACITY, LINE-NO
    ((= (strcase category) "TANKS")
     (if equipment-type (c:update-block-attribute block-ent "EQUIPMENT-TYPE" equipment-type))
     (if manufacturer (c:update-block-attribute block-ent "MANUFACTURER" manufacturer))
     (if model-no (c:update-block-attribute block-ent "MODEL-NO" model-no))
     (if equipment-no (c:update-block-attribute block-ent "EQUIPMENT-NO" equipment-no))
     (if capacity (c:update-block-attribute block-ent "CAPACITY" capacity))
     (if line-no (c:update-block-attribute block-ent "LINE-NO" line-no)))
    
    ;; EQUIPMENT: EQUIPMENT-TYPE, MANUFACTURER, MODEL-NO, EQUIPMENT-NO, LINE-NO
    ((= (strcase category) "EQUIPMENT")
     (if equipment-type (c:update-block-attribute block-ent "EQUIPMENT-TYPE" equipment-type))
     (if manufacturer (c:update-block-attribute block-ent "MANUFACTURER" manufacturer))
     (if model-no (c:update-block-attribute block-ent "MODEL-NO" model-no))
     (if equipment-no (c:update-block-attribute block-ent "EQUIPMENT-NO" equipment-no))
     (if line-no (c:update-block-attribute block-ent "LINE-NO" line-no)))
    
    ;; Default for other equipment categories
    (t
     (if equipment-type (c:update-block-attribute block-ent "EQUIPMENT-TYPE" equipment-type))
     (if manufacturer (c:update-block-attribute block-ent "MANUFACTURER" manufacturer))
     (if model-no (c:update-block-attribute block-ent "MODEL-NO" model-no))
     (if equipment-no (c:update-block-attribute block-ent "EQUIPMENT-NO" equipment-no))
     (if line-no (c:update-block-attribute block-ent "LINE-NO" line-no)))
  )
  
  (princ (strcat "\nInserted " symbol-name " with CTO attributes"))
)

;; Batch update attributes in an area
(defun c:batch-update-attrib (x1 y1 x2 y2 tag-name prefix / corner1 corner2)
  "Update all instances of an attribute in a rectangular area"
  (setq corner1 (list x1 y1 0.0))
  (setq corner2 (list x2 y2 0.0))
  
  ;; Use ATTEDIT with window selection
  (command "_.-ATTEDIT" "_Y" "" "" "" "_W" corner1 corner2 tag-name)
  
  ;; Note: In batch mode, we can't easily provide unique values
  ;; This would need to be done one at a time for unique values
  (princ "\nBatch attribute edit initiated - complete manually")
)

;; Quick attribute value reader (for verification)
(defun c:read-attrib-at-point (x y / pt)
  "Display attribute values at a point (uses LIST command)"
  (setq pt (list x y 0.0))
  (command "_LIST" pt "")
  (princ "\nAttribute values displayed in text window")
)

;; Insert valve with correct CTO attributes
(defun c:insert-valve-with-attributes (x y valve-type equipment-type manufacturer model-no va-size va-no line-no / symbol-name block-path block-ent)
  "Insert a valve with proper CTO block attributes"
  ;; Map valve type to symbol
  (cond
    ((= (strcase valve-type) "GATE") (setq symbol-name "VA-GATE"))
    ((= (strcase valve-type) "GLOBE") (setq symbol-name "VA-GLOBE"))
    ((= (strcase valve-type) "CHECK") (setq symbol-name "VA-CHECK"))
    ((= (strcase valve-type) "BALL") (setq symbol-name "VA-BALL"))
    ((= (strcase valve-type) "BUTTERFLY") (setq symbol-name "VA-BUTTERFLY"))
    (t (setq symbol-name "VA-GATE"))
  )
  
  ;; Build path and insert
  (setq block-path (strcat "C:/PIDv4-CTO/VALVES/" symbol-name ".dwg"))
  (c:insert-block-simple block-path x y 1.0 0)
  
  ;; Get the inserted block and update CTO valve attributes
  (setq block-ent (entlast))
  
  ;; VALVES: EQUIPMENT-TYPE, MANUFACTURER, MODEL-NO, VA-SIZE, VA-NO, LINE-NO
  (if equipment-type (c:update-block-attribute block-ent "EQUIPMENT-TYPE" equipment-type))
  (if manufacturer (c:update-block-attribute block-ent "MANUFACTURER" manufacturer))
  (if model-no (c:update-block-attribute block-ent "MODEL-NO" model-no))
  (if va-size (c:update-block-attribute block-ent "VA-SIZE" va-size))
  (if va-no (c:update-block-attribute block-ent "VA-NO" va-no))
  (if line-no (c:update-block-attribute block-ent "LINE-NO" line-no))
  
  (princ (strcat "\nInserted " valve-type " valve: " va-no))
)

;; Insert equipment tag annotation block
(defun c:insert-equipment-tag (x y equipment-tag / block-path block-ent)
  "Insert ANNOT-EQUIP_TAG block with EQUIP_NUMBER attribute"
  (setq block-path "C:/PIDv4-CTO/ANNOTATION/ANNOT-EQUIP_TAG.dwg")
  
  ;; Insert tag block
  (c:insert-block-simple block-path x y 1.0 0)
  
  ;; Get the inserted block and update attribute
  (setq block-ent (entlast))
  (if equipment-tag (c:update-block-attribute block-ent "EQUIP_NUMBER" equipment-tag))
  
  (princ (strcat "\nInserted equipment tag: " equipment-tag))
)

;; Insert equipment description annotation block
(defun c:insert-equipment-description (x y equipment-name description1 description2 description3 description4 description5 description6 / block-path block-ent underlined-name)
  "Insert ANNOT-EQUIP_DESCR block with EQUIP and DESCR1-6 attributes"
  (setq block-path "C:/PIDv4-CTO/ANNOTATION/ANNOT-EQUIP_DESCR.dwg")
  
  ;; Insert description block
  (c:insert-block-simple block-path x y 1.0 0)
  
  ;; Get the inserted block and update attributes
  (setq block-ent (entlast))
  
  ;; Add underline formatting to equipment name
  (if equipment-name 
    (progn
      (setq underlined-name (strcat "%%u" equipment-name))
      (c:update-block-attribute block-ent "EQUIP" underlined-name)
    )
  )
  
  ;; Update description fields (DESCR1 is reserved for tag number)
  (if description1 (c:update-block-attribute block-ent "DESCR1" description1))
  (if description2 (c:update-block-attribute block-ent "DESCR2" description2))
  (if description3 (c:update-block-attribute block-ent "DESCR3" description3))
  (if description4 (c:update-block-attribute block-ent "DESCR4" description4))
  (if description5 (c:update-block-attribute block-ent "DESCR5" description5))
  (if description6 (c:update-block-attribute block-ent "DESCR6" description6))
  
  (princ (strcat "\nInserted equipment description: " equipment-name))
)

;; Insert line number annotation block
(defun c:insert-line-number (x y line-number / block-path block-ent)
  "Insert ANNOT-LINE_NUMBER block with LINE_NUMBER attribute"
  (setq block-path "C:/PIDv4-CTO/ANNOTATION/ANNOT-LINE_NUMBER.dwg")
  
  ;; Insert line number block
  (c:insert-block-simple block-path x y 1.0 0)
  
  ;; Get the inserted block and update attribute
  (setq block-ent (entlast))
  (if line-number (c:update-block-attribute block-ent "LINE_NUMBER" line-number))
  
  (princ (strcat "\nInserted line number: " line-number))
)

;; Insert instrument with proper attributes
(defun c:insert-instrument-with-tag (x y inst-type tag-id range / symbol-name category block-path block-ent)
  "Insert an instrument with proper block attributes"
  ;; Map instrument type to symbol
  (cond
    ((= (strcase inst-type) "PRESSURE") (setq symbol-name "ELEC-PRESS_SW_ACT"))
    ((= (strcase inst-type) "TEMPERATURE") (setq symbol-name "ELEC-TEMP_SW_ACT"))
    ((= (strcase inst-type) "FLOW") (setq symbol-name "PRIMELEM-ORIFICE_PLATE"))
    ((= (strcase inst-type) "LEVEL") (setq symbol-name "ELEC-LIQ_LEV_SW_ACT"))
    (t (setq symbol-name "INST-DISC-FLDACCESS"))
  )
  
  ;; Determine category based on symbol prefix
  (cond
    ((= (substr symbol-name 1 5) "ELEC-") (setq category "ELECTRICAL"))
    ((= (substr symbol-name 1 9) "PRIMELEM-") (setq category "PRIMARY_ELEMENTS"))
    (t (setq category "INSTRUMENTS"))
  )
  
  ;; Build path and insert
  (setq block-path (strcat "C:/PIDv4-CTO/" category "/" symbol-name ".dwg"))
  (c:insert-block-simple block-path x y 0.75 0)
  
  ;; Get the inserted block and update attributes
  (setq block-ent (entlast))
  
  (if (and tag-id (> (strlen tag-id) 0))
    (progn
      (c:update-block-attribute block-ent "TAG" tag-id)
      (c:update-block-attribute block-ent "INSTRUMENT_TAG" tag-id)
      (c:update-block-attribute block-ent "ID" tag-id)
    )
  )
  
  (if (and range (> (strlen range) 0))
    (progn
      (c:update-block-attribute block-ent "RANGE" range)
      (c:update-block-attribute block-ent "SCALE" range)
      (c:update-block-attribute block-ent "DESCRIPTION" range)
    )
  )
  
  (princ (strcat "\nInserted " inst-type " instrument: " tag-id))
)

;; List all attributes in a block (debugging utility)
(defun c:list-block-attributes (block-ent / ent ent-data)
  "List all attribute tags and values in a block"
  (setq ent (entnext block-ent))  ; Get first subentity
  (princ "\nBlock Attributes:")
  
  ;; Loop through all subentities
  (while ent
    (setq ent-data (entget ent))
    
    ;; Check if this is an attribute entity
    (if (= (cdr (assoc 0 ent-data)) "ATTRIB")
      (progn
        (princ (strcat "\n  Tag: " (cdr (assoc 2 ent-data))
                       " Value: " (cdr (assoc 1 ent-data))))
      )
    )
    ;; Move to next subentity
    (setq ent (entnext ent))
  )
  (princ "\n")
)

;; Helper function to edit last inserted block using entity modification
(defun c:edit-last-block-attrib (tag-name new-value / block-ent)
  "Edit an attribute on the last inserted block using entget/entmod"
  (setq block-ent (entlast))
  (c:update-block-attribute block-ent tag-name new-value)
)

;; Insert block from path with a list of attribute values (in order)
(defun c:insert-block-with-attribs (block-path x y scale rotation attrib-values / block-ent ent ent-data idx)
  "Insert a block and set attribute values from a list"
  (c:insert-block-simple block-path x y scale rotation)
  (setq block-ent (entlast))
  (setq ent (entnext block-ent))
  (setq idx 0)
  (while (and ent attrib-values (< idx (length attrib-values)))
    (setq ent-data (entget ent))
    (if (= (cdr (assoc 0 ent-data)) "ATTRIB")
      (progn
        (setq new-data (subst (cons 1 (nth idx attrib-values)) (assoc 1 ent-data) ent-data))
        (entmod new-data)
        (entupd ent)
        (setq idx (1+ idx))
      )
    )
    (setq ent (entnext ent))
  )
  (princ (strcat "\nInserted block with " (itoa idx) " attributes"))
)

;; Update block attribute by finding nearest block to a point
(defun c:update-block-attribs-at-point (x y tag-name new-value / pt ss min-dist min-ent i ent ent-data ins-pt dist)
  "Find nearest INSERT entity to (x,y) and update its attribute"
  (setq pt (list x y 0.0))
  (setq ss (ssget "X" '((0 . "INSERT"))))
  (if (not ss)
    (progn (princ "\nNo blocks found in drawing") (exit))
  )
  (setq min-dist 1e30 min-ent nil i 0)
  (while (< i (sslength ss))
    (setq ent (ssname ss i))
    (setq ent-data (entget ent))
    (setq ins-pt (cdr (assoc 10 ent-data)))
    (setq dist (distance pt ins-pt))
    (if (< dist min-dist)
      (progn (setq min-dist dist) (setq min-ent ent))
    )
    (setq i (1+ i))
  )
  (if min-ent
    (c:update-block-attribute min-ent tag-name new-value)
    (princ "\nNo block found near specified point")
  )
)

(princ "\nAttribute tools loaded successfully\n")