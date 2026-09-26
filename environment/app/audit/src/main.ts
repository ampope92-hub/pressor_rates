// Weekly pressor titration audit. Reads the week from the interface engine at ENGINE_HOST and
// ENGINE_PORT and prints the report SPEC.md describes as one line of JSON on standard output.
//
// Not written yet: this prints an empty report.
process.stdout.write(JSON.stringify({ orders: {} }) + "\n");
