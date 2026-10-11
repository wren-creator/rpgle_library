**free
// CUSTREPORT.RPGLE -- prints every customer and a total, a page at a
// time. The shape every RPG report has:
//
//   * write the heading
//   * loop: if the overflow indicator came on, write the heading again
//     (its SKIPB(1) starts the new page) and turn the indicator off
//   * write a detail line
//   * write the total
//
// The overflow indicator comes on when a line at or past the overflow
// line is printed. rpgle's defaults are 66 lines to a page and overflow
// at line 60; make a short page to see it happen:
//
//   rpgle run programs/custreport.rpgle --db shop.db --page-length 8 --overflow-line 6
//   rpgle run programs/custreport.rpgle --db shop.db --spool-dir spool

dcl-f custmast keyed;
dcl-f custrpt printer oflind(*inof);

dcl-s tot packed(9:2);

write heading;

setll *loval custmast;
read custmast;
dow not %eof(custmast);
  if *inof;
    write heading;
    *inof = *off;
  endif;
  tot += bal;
  write detail;
  read custmast;
enddo;

write total;
*inlr = *on;
return;
