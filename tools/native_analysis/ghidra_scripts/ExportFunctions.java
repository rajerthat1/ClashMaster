// Export function boundaries to CSV: entry,size,name
// Usage: -postScript ExportFunctions.java /path/to/functions.csv
import java.io.FileWriter;
import java.io.PrintWriter;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportFunctions extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: ExportFunctions.java <output.csv>");
            return;
        }
        try (PrintWriter out = new PrintWriter(new FileWriter(args[0]))) {
            out.println("entry,size,name");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                Function function = functions.next();
                out.println(String.format("0x%08x,%d,%s",
                    function.getEntryPoint().getOffset(),
                    function.getBody().getNumAddresses(),
                    function.getName()));
            }
        }
        println("wrote " + args[0]);
    }
}
