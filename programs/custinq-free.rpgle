**free
// CUSTINQ-FREE.RPGLE -- the same customer inquiry as custinq.rpgle,
// written free-form. Compare the two: no columns, no result indicators.
// The not-found test is %FOUND, and the display file is named in a
// dcl-f instead of an F-spec. Uses custinq.dspf and custmast.pf:
//
//   rpgle run programs/custinq-free.rpgle --dspf programs/custinq.dspf --db shop.db

ctl-opt dftactgrp(*no);

dcl-f custmast keyed;
dcl-f custinq workstn;

exfmt inq;
dow not *in03;
  *in90 = *off;
  chain custno custmast;
  if not %found(custmast);
    *in90 = *on;
    msgtxt = 'Customer not found';
    name = ' ';
    bal = 0;
  endif;
  exfmt inq;
enddo;

*inlr = *on;
return;
