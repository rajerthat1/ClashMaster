// Decompile functions containing the given addresses.
// Usage: -postScript DecompileAt.java <output.c> <addr> [addr...]
import java.io.FileWriter;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: DecompileAt.java <output.c> <addr> [addr...]");
            return;
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new FileWriter(args[0]))) {
            for (int i = 1; i < args.length; i++) {
                String text = args[i].startsWith("0x") ? args[i].substring(2) : args[i];
                Address address = toAddr(Long.parseLong(text, 16));
                Function function = getFunctionContaining(address);
                if (function == null) {
                    out.println("// ==== " + args[i] + " : no function ====");
                    continue;
                }
                out.println("// ==== " + function.getEntryPoint() + " " + function.getName() + " ====");
                DecompileResults results = decompiler.decompileFunction(function, 60, monitor);
                if (!results.decompileCompleted()) {
                    out.println("// decompile failed: " + results.getErrorMessage());
                    continue;
                }
                out.println(results.getDecompiledFunction().getC());
            }
        }
        println("wrote " + args[0]);
    }
}
