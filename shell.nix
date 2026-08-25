{ pkgs ? import <nixpkgs> {
    config.allowUnfree = true;
  }
}:

pkgs.mkShell {
  packages = [
    pkgs.python311
    pkgs.python311Packages.virtualenv
    pkgs.git
    pkgs.zlib
  ];

  shellHook = ''
    export MPLBACKEND=Agg
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}:/run/opengl-driver/lib:''${LD_LIBRARY_PATH:-}"
  '';
}
