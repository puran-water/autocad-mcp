# AutoCAD LT AutoLISP MCP Server

This MCP server enables natural language control of AutoCAD LT 2024/2025 through AutoLISP code generation and execution. It bridges Claude or other LLM clients with AutoCAD LT for creating engineering drawings, P&ID diagrams, and technical documentation through conversational prompts.


> **⚠️ DEVELOPMENT STATUS: This project is under active development and is not yet production-ready. APIs, interfaces, and functionality may change without notice. Use at your own risk for evaluation and testing purposes only. Not recommended for production deployments.**

## 🚀 Key Features

### Core Drawing Operations
- Generates and executes AutoLISP code in AutoCAD LT 2024+
- Creates basic shapes (lines, circles, polylines, text)
- Handles block insertion with comprehensive attribute management
- Supports advanced geometry (arcs, ellipses, rectangles)
- Provides robust layer creation and management
- Creates hatches and dimensions for technical drawings

### P&ID and Process Engineering (NEW)
- **CTO Library Integration**: Complete access to CAD Tools Online P&ID Symbol Library (600+ ISA 5.1-2009 standard symbols)
- **Intelligent Attribute Handling**: Proper block attributes for equipment schedules and valve lists
- **Process Equipment**: Pumps, valves, tanks, instruments, and process equipment
- **Standard Annotations**: Equipment tags, descriptions, and line numbers
- **Layer Management**: Industry-standard P&ID layer organization

### Performance Optimizations
- **Fast Mode**: Optimized execution with minimal delays (80% speed improvement)
- **Batch Operations**: Create multiple objects in single operations
- **Clipboard Integration**: Efficient large script execution
- **Smart Loading**: Essential LISP files only for faster startup

## 📋 Prerequisites

- **AutoCAD LT 2024 or newer** (with AutoLISP support)
- **Python 3.10 or higher**
- **Claude Desktop** or other MCP client application
- **Optional**: CAD Tools Online P&ID Symbol Library (for full P&ID functionality)

## 🛠️ Setup Instructions

### 1. Install Dependencies
```bash
git clone https://github.com/hvkshetry/autocad-mcp.git
cd autocad-mcp
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Claude Desktop

**For users WITH CTO Library:**
```json
{
  "mcpServers": {
    "autocad-mcp": {
      "command": "path\\to\\autocad-mcp\\venv\\Scripts\\python.exe",
      "args": ["path\\to\\autocad-mcp\\server_lisp_fast.py"]
    }
  }
}
```

**For users WITHOUT CTO Library:**
```json
{
  "mcpServers": {
    "autocad-mcp": {
      "command": "path\\to\\autocad-mcp\\venv\\Scripts\\python.exe",
      "args": ["path\\to\\autocad-mcp\\server_lisp.py"]
    }
  }
}
```

### 3. CTO Library Setup (Optional but Recommended)

**If you have the CAD Tools Online P&ID Library:**
1. Install the CTO library to `C:\PIDv4-CTO\`
2. Verify the following directories exist:
   - `C:\PIDv4-CTO\VALVES\`
   - `C:\PIDv4-CTO\EQUIPMENT\`
   - `C:\PIDv4-CTO\PUMPS-BLOWERS\`
   - `C:\PIDv4-CTO\TANKS\`
   - (and other standard categories)

**If you DON'T have the CTO Library:**
- Use `server_lisp.py` instead of `server_lisp_fast.py`
- P&ID tools will not work, but all basic drawing operations remain available
- You can still create custom blocks and use standard AutoCAD functionality

### 4. Start AutoCAD and Server
1. **Launch AutoCAD LT**
   - Create or open a drawing
   - Ensure command line is visible

2. **Start the Server**
   ```bash
   # For users with CTO library (recommended)
   start_fast_server.bat
   
   # For users without CTO library
   start_lisp_server.bat
   ```

3. **Test the Connection**
   ```bash
   test_connection.bat
   ```

## 🔧 Available Tools

### Basic Drawing Tools
- `create_line`: Draw lines between points
- `create_circle`: Create circles with center and radius
- `create_text`: Add text labels with rotation support
- `create_polyline`: Create polylines from point series
- `create_rectangle`: Create rectangles from corner points
- `batch_create_lines`: Create multiple lines efficiently
- `batch_create_circles`: Create multiple circles efficiently
- `batch_create_texts`: Create multiple text entities efficiently

### Block and Layer Management
- `insert_block`: Insert blocks with attributes and positioning
- `set_layer_properties`: Create/modify layers with full properties
- `move_last_entity`: Move recently created entities
- `update_block_attribute`: Modify block attributes after insertion

### P&ID and Process Tools (CTO Library Required)
- `setup_pid_layers`: Create standard P&ID drawing layers
- `insert_pid_symbol`: Insert any symbol from CTO library
- `insert_pid_equipment_with_attribs`: Insert equipment with proper attributes
- `insert_valve_with_attributes`: Insert valves with CTO attributes
- `insert_equipment_tag`: Add equipment identification tags
- `insert_equipment_description`: Add equipment description blocks
- `insert_line_number_tag`: Add process line identification
- `draw_process_line`: Draw process piping between points
- `connect_equipment`: Connect equipment with orthogonal routing
- `add_flow_arrow`: Add directional flow indicators

### Advanced Operations
- `create_arc`: Create arcs with center, radius, and angles
- `create_ellipse`: Create ellipses with major axis and ratio
- `create_mtext`: Add multiline formatted text
- `create_linear_dimension`: Add linear dimensions
- `create_hatch`: Add hatching to closed areas
- `list_pid_symbols`: List available symbols by category

## 📖 Usage Examples

### Basic Drawing
```
"Draw a line from (0,0) to (100,100)"
"Create a circle at (50,50) with radius 25"
"Add text 'Equipment Room' at position (75,125)"
```

### P&ID Creation (with CTO Library)
```
"Set up P&ID layers for a new drawing"
"Insert a centrifugal pump at (100,100) with tag P-101"
"Add a gate valve at (150,100) with size 6 inches and tag V-201"
"Connect the pump P-101 to valve V-201 with process piping"
"Add equipment tag P-101 above the pump"
```

### Process Flow Creation
```
"Create a wastewater treatment process with screening, primary clarifier, aeration basin, and secondary clarifier"
"Insert a clarifier at (200,100) with equipment number CL-301"
"Add line number 8"-WW-001 to the main process line"
```

### Batch Operations
```
"Create 10 circles in a grid pattern with centers every 50 units"
"Draw process lines connecting all equipment in sequence"
"Add equipment tags for all pumps with sequential numbering"
```

## 🚧 Configuration for Users Without CTO Library

If you don't have the CAD Tools Online library, you can still use this MCP server effectively:

### 1. Use the Standard Server
Replace the server command with:
```json
"args": ["path\\to\\autocad-mcp\\server_lisp.py"]
```

### 2. Modify P&ID Tool Calls
The following tools will not work without CTO library:
- `insert_pid_symbol`
- `insert_pid_equipment_with_attribs`
- `insert_valve_with_attributes`
- `list_pid_symbols`

### 3. Alternative Approaches
Instead of CTO symbols, you can:
- Use standard AutoCAD blocks
- Create simple geometric representations
- Use text labels for equipment identification
- Import your own symbol library

### 4. Example Alternative Usage
```
"Draw a circle to represent a pump at (100,100)"
"Add text 'P-101 Centrifugal Pump' next to it"
"Create a rectangle for a tank at (200,100)"
"Connect them with a line for piping"
```

## 📁 LISP Library Structure

The server loads these LISP files for functionality:

| File | Purpose | Dependencies |
|------|---------|--------------|
| `error_handling.lsp` | Base error handling and validation | None (load first) |
| `basic_shapes.lsp` | Core drawing functions | error_handling.lsp |
| `batch_operations.lsp` | Batch creation tools | basic_shapes.lsp |
| `drafting_helpers.lsp` | Block and layer management | basic_shapes.lsp |
| `pid_tools.lsp` | P&ID specific operations | drafting_helpers.lsp |
| `attribute_tools.lsp` | Block attribute handling | pid_tools.lsp |
| `advanced_geometry.lsp` | Extended geometry creation | basic_shapes.lsp |
| `advanced_entities.lsp` | Complex entity creation | advanced_geometry.lsp |
| `annotation_helpers.lsp` | Text and dimension tools | basic_shapes.lsp |
| `entity_modification.lsp` | Entity manipulation | drafting_helpers.lsp |

## ⚡ Performance Optimization

### Fast Mode Benefits
- **80% faster** drawing operations using batch tools
- **90% faster** server initialization with essential files only
- **Clipboard-based** script execution for complex operations

### Batch Operation Examples
```python
# Instead of 10 separate create_line calls (8 seconds)
batch_create_lines([
    [0, 0, 100, 0],
    [100, 0, 100, 100],
    [100, 100, 0, 100],
    # ... more lines
])  # Single call (0.8 seconds)
```

### Performance Settings
Adjust performance based on your system:
```python
# Fast systems
set_performance_mode(fast_mode=True, minimal_delay=0.03)

# Slower systems (more reliable)
set_performance_mode(fast_mode=False, minimal_delay=0.1)
```

## 🔍 Troubleshooting

Common issues and solutions:

### LISP Loading Issues
- **Problem**: "LOAD failed" errors
- **Solution**: Add LISP directory to AutoCAD trusted paths
- **See**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#1-lisp-files-fail-to-load)

### CTO Library Issues
- **Problem**: "File not found" for P&ID symbols
- **Solution**: Verify CTO installation path or use standard server
- **Alternative**: Use `server_lisp.py` without P&ID functionality

### Window Focus Problems
- **Problem**: Commands not reaching AutoCAD
- **Solution**: Ensure AutoCAD is active, run as Administrator
- **See**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#window-focus-issues)

## 📊 What's New in This Version

### Version 2.0 Features
- ✅ **Complete CTO Library Integration** (600+ P&ID symbols)
- ✅ **Intelligent Block Attributes** for equipment schedules
- ✅ **Fast Mode Server** with 80% performance improvement
- ✅ **Batch Operations** for multiple object creation
- ✅ **Process Engineering Tools** for P&ID creation
- ✅ **Industry Standard Layers** for professional drawings
- ✅ **Enhanced Error Handling** and troubleshooting guides

### Migration from v1.0
Users of the original version should:
1. Update to `server_lisp_fast.py` for better performance
2. Add CTO library path if available
3. Review new P&ID tools for process drawings
4. Update Claude Desktop configuration

## 📄 License

MIT License - See LICENSE file for details

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Test with both CTO and non-CTO configurations
4. Submit a pull request with clear documentation

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/hvkshetry/autocad-mcp/issues)
- **Documentation**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Performance**: [PERFORMANCE_OPTIMIZATION.md](PERFORMANCE_OPTIMIZATION.md)

---

*For detailed troubleshooting and advanced configuration, see the additional documentation files in this repository.*