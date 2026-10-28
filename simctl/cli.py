import typer

app = typer.Typer(help="opti-sim control CLI")


@app.command()
def doctor() -> None:
    """Host checks and tuning suggestions (not implemented yet)."""
    typer.echo("not implemented")


if __name__ == "__main__":
    app()
