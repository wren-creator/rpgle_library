**free
// CUSTINQ-COPY.RPGLE -- custinq-free.rpgle with its file declarations
// moved into a copybook. Run it with the display file named, since the
// copybook, not this program, owns the dcl-f lines:
//
//   rpgle run programs/custinq-copy.rpgle --dspf programs/custinq.dspf --db shop.db

/copy qrpglesrc,custfiles

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
