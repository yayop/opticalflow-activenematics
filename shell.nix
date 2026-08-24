{ pkgs ? import <nixpkgs> {
    config.allowUnfree = true;
  }
}:

pkgs.mkShell {
  packages = [
    pkgs.python311
    pkgs.python311Packages.virtualenv
    pkgs.git
  ];

  shellHook = ''
    export MPLBACKEND=Agg
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ]}:/run/opengl-driver/lib:''${LD_LIBRARY_PATH:-}"
  '';
}
