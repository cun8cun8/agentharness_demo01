from mcp.server.fastmcp import FastMCP

server = FastMCP("researchforge-test")


@server.tool()
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    server.run(transport="stdio")
