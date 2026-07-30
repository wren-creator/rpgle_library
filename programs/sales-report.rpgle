     * SALES-REPORT.RPGLE -- computes each region's revenue and the
     * grand total, then displays it as a single-screen business
     * report. Fixed-form RPG IV, V4R3-safe.

     FREPORT    CF   E             WORKSTN

     DPRICE1           S              5  0 INZ(42)
     DPRICE2           S              5  0 INZ(58)
     DPRICE3           S              5  0 INZ(37)
     DPRICE4           S              5  0 INZ(61)
     DPRICE5           S              5  0 INZ(49)

     C                   EVAL      units1 = 1200
     C                   EVAL      rev1 = units1 * price1
     C                   EVAL      units2 = 950
     C                   EVAL      rev2 = units2 * price2
     C                   EVAL      units3 = 1800
     C                   EVAL      rev3 = units3 * price3
     C                   EVAL      units4 = 640
     C                   EVAL      rev4 = units4 * price4
     C                   EVAL      units5 = 1075
     C                   EVAL      rev5 = units5 * price5
     C                   EVAL      totunit = units1 + units2 + units3 + units4 + units5
     C                   EVAL      totrev = rev1 + rev2 + rev3 + rev4 + rev5
     C                   EXFMT     REPORT
     C                   EVAL      *inlr = *on
     C                   RETURN
