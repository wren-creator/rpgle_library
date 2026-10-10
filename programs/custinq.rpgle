     * CUSTINQ.RPGLE -- customer inquiry. Shows a screen, CHAINs to the
     * CUSTMAST file by the customer number typed, and displays what it
     * finds. The classic RPG shape: read the key, CHAIN, branch on the
     * not-found indicator (71). Fixed-form RPG IV, V4R3-safe.

     FCUSTMAST  IF   E           K DISK
     FCUSTINQ   CF   E             WORKSTN

     C                   EXFMT     INQ
     C                   DOW       *IN03 = *OFF
     C                   EVAL      *IN90 = *OFF
     C     CUSTNO        CHAIN     CUSTMAST                           71
     C   71              EVAL      *IN90 = *ON
     C   71              EVAL      MSGTXT = 'Customer not found'
     C   71              EVAL      NAME = ' '
     C   71              EVAL      BAL = 0
     C                   EXFMT     INQ
     C                   ENDDO
     C                   EVAL      *INLR = *ON
     C                   RETURN
