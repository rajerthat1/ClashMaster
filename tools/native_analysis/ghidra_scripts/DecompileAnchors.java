// Force a function at each address, then decompile it.
//
// DecompileAt.java relies on Ghidra's auto-analysis having already created the
// function.  Stale/incomplete projects often have real function starts that
// were never split, so this script disassembles the address and creates the
// function before decompiling.
//
// Usage: -postScript DecompileAnchors.java <output.c> <addr> [addr...]
import java.io.FileWriter;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileAnchors extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: DecompileAnchors.java <output.c> <addr> [addr...]");
            return;
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new FileWriter(args[0]))) {
            for (int i = 1; i < args.length; i++) {
                String text = args[i].startsWith("0x") ? args[i].substring(2) : args[i];
                Address address = toAddr(Long.parseLong(text, 16));
                Function function = getFunctionContaining(address);
                if (function == null || !function.getEntryPoint().equals(address)) {
                    disassemble(address);
                    function = createFunction(address, null);
                }
                if (function == null) {
                    out.println("// ==== " + args[i] + " : could not create function ====");
                    continue;
                }
                out.println("// ==== " + function.getEntryPoint() + " " + function.getName() + " ====");
                DecompileResults results = decompiler.decompileFunction(function, 120, monitor);
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
